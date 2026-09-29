from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.test import TestCase
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe


def _make_open_run(hearth=None):
    lot = ResinLot.objects.create(
        lotCode="脂-TEST-001",
        originPlace="测试沟",
        arrivalKg=Decimal("100.00"),
        receivedAt=timezone.now(),
    )
    hearth = hearth or FireHearth.objects.create(
        lane=1, tag="测试灶-甲", resinGrade="测试脂", phase="holding"
    )
    return CookRun.objects.create(
        hearth=hearth,
        resinLot=lot,
        openedAt=timezone.now(),
        targetSoftPointC=Decimal("90.00"),
    ), hearth


def _probe_payload(value, name="测试工"):
    return {
        "sampledAt": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        "softPointC": value,
        "samplerName": name,
    }


class SoftPointModelTests(TestCase):
    def setUp(self):
        self.run, _ = _make_open_run()

    def test_save_rejects_out_of_range_without_row(self):
        for bad in ("39.99", "120.01", "0", "999"):
            with self.subTest(bad=bad):
                before = SoftPointProbe.objects.count()
                probe = SoftPointProbe(
                    run=self.run,
                    sampledAt=timezone.now(),
                    softPointC=Decimal(bad),
                    samplerName="测试工",
                )
                with self.assertRaises(ValidationError):
                    probe.save()
                self.assertEqual(SoftPointProbe.objects.count(), before)
                self.assertIsNone(probe.pk)

    def test_save_accepts_boundaries(self):
        for ok in ("40", "120", "95", "102.40"):
            SoftPointProbe.objects.create(
                run=self.run,
                sampledAt=timezone.now(),
                softPointC=Decimal(ok),
                samplerName="测试工",
            )
        self.assertEqual(self.run.probes.count(), 4)

    def test_db_check_constraint_blocks_bypass_insert(self):
        # 即使绕过模型层（裸 SQL / .update 之外的插入），库约束也必须拦住越界值
        with connection.cursor() as cur:
            with self.assertRaises(IntegrityError):
                cur.execute(
                    "INSERT INTO kiln_softpointprobe "
                    "(run_id, sampledAt, softPointC, samplerName) "
                    "VALUES (%s, %s, %s, %s)",
                    [self.run.pk, timezone.now().isoformat(), "200", "越界者"],
                )
        self.assertEqual(self.run.probes.count(), 0)


class AddProbeViewTests(TestCase):
    def setUp(self):
        self.run, self.hearth = _make_open_run()
        self.user = self._login()

    def _login(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("worker", password="x")
        self.client.force_login(user)
        return user

    def _post(self, value, htmx=True):
        headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        return self.client.post(
            f"/hearth/{self.hearth.pk}/probe/",
            _probe_payload(value),
            **headers,
        )

    def test_valid_probe_persists_exactly_one_row(self):
        resp = self._post("88.5")
        self.assertEqual(resp.status_code, 200)
        probes = list(self.run.probes.all())
        self.assertEqual(len(probes), 1)
        self.assertEqual(probes[0].softPointC, Decimal("88.50"))

    def test_out_of_range_creates_zero_rows(self):
        for bad in ("120.01", "200", "39", "-5"):
            with self.subTest(bad=bad):
                before = SoftPointProbe.objects.count()
                resp = self._post(bad)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(SoftPointProbe.objects.count(), before)

    def test_garbage_input_creates_zero_rows(self):
        # 回归：旧逻辑在表单失效后仍用 POST 原值强存残行
        before = SoftPointProbe.objects.count()
        resp = self._post("abc")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SoftPointProbe.objects.count(), before)
        self.assertContains(resp, "error", status_code=200)

    def test_empty_value_creates_zero_rows(self):
        before = SoftPointProbe.objects.count()
        resp = self._post("")
        self.assertEqual(SoftPointProbe.objects.count(), before)
        self.assertEqual(resp.status_code, 200)

    def test_timeline_count_matches_db_rows(self):
        """验收核心：时间线条数 == 真实入库条数，无论夹杂多少次失败。"""
        attempts = ["88.0", "200", "92.5", "abc", "96.2", "30", ""]
        db_rows = 0
        for value in attempts:
            resp = self._post(value)
            timeline = resp.content.decode()
            persisted = self.run.probes.count()
            rendered = timeline.count('class="mono temp"')
            self.assertEqual(
                rendered, persisted,
                f"提交 {value!r} 后时间线 {rendered} 行 != 库里 {persisted} 行",
            )
            db_rows = persisted
        self.assertEqual(db_rows, 3)

    def test_timeline_shows_true_value_not_clamped(self):
        # 102 在 40~120 合法区间内但高于出胶门槛 95，必须原样显示而非夹成 95
        resp = self._post("102.40")
        body = resp.content.decode()
        self.assertIn("102.40", body)
        self.assertIn('class="hot"', body)
        # 不得出现把真实值伪装成 95 的渲染
        self.assertNotIn(">95℃<", body.replace(" ", ""))


class EditProbeViewTests(TestCase):
    def setUp(self):
        self.run, self.hearth = _make_open_run()
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("worker", password="x")
        self.client.force_login(user)
        self.probe = SoftPointProbe.objects.create(
            run=self.run,
            sampledAt=timezone.now() - timezone.timedelta(hours=1),
            softPointC=Decimal("88.00"),
            samplerName="测试工",
        )

    def _edit(self, value, htmx=True):
        headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        return self.client.post(
            f"/probes/{self.probe.pk}/edit/",
            _probe_payload(value),
            **headers,
        )

    def test_valid_update_changes_value(self):
        self._edit("91.00")
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.softPointC, Decimal("91.00"))
        self.assertEqual(SoftPointProbe.objects.count(), 1)

    def test_out_of_range_update_keeps_old_value(self):
        resp = self._edit("200")
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.softPointC, Decimal("88.00"))
        self.assertEqual(SoftPointProbe.objects.count(), 1)
        self.assertContains(resp, "error")

    def test_garbage_update_keeps_old_value(self):
        self._edit("abc", htmx=False)
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.softPointC, Decimal("88.00"))
        self.assertEqual(SoftPointProbe.objects.count(), 1)

    def test_failed_htmx_update_timeline_still_reflects_db(self):
        resp = self._edit("999")
        rendered = resp.content.decode().count('class="mono temp"')
        self.assertEqual(rendered, self.run.probes.count())
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.softPointC, Decimal("88.00"))
