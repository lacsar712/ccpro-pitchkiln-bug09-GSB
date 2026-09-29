"""探针保存的验收测试。

核心验收标准：时间线条数必须等于真实入库条数；
校验通过才写库、失败零残行、禁止展示层夹值掩盖库内越界。
"""
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, IntegrityError
from django.test import TestCase, Client
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe

TIMELINE_ROW = re.compile(r'<li class="(?:ok|hot)"')


def make_open_run(tag="灶-甲", lane=1):
    lot = ResinLot.objects.create(
        lotCode=f"脂-{tag}",
        originPlace="测试地",
        arrivalKg=Decimal("100.00"),
        receivedAt=timezone.now(),
    )
    hearth = FireHearth.objects.create(
        lane=lane, tag=tag, resinGrade="测试级", phase=FireHearth.PHASE_HOLDING
    )
    run = CookRun.objects.create(
        hearth=hearth,
        resinLot=lot,
        openedAt=timezone.now(),
        targetSoftPointC=Decimal("88.00"),
    )
    return hearth, run


def timeline_row_count(html):
    return len(TIMELINE_ROW.findall(html))


def timeline_section(html):
    # 只取时间线 <ol>，不含下方登记表单（表单失败时会回显用户原始输入）
    start = html.find('<ol class="probe-timeline">')
    end = html.find("</ol>", start)
    return html[start:end] if start != -1 else ""


class ProbeModelTests(TestCase):
    def setUp(self):
        self.hearth, self.run = make_open_run()

    def _probe(self, value):
        return SoftPointProbe(
            run=self.run,
            sampledAt=timezone.now(),
            softPointC=Decimal(str(value)),
            samplerName="测试员",
        )

    def test_out_of_range_create_raises_and_persists_nothing(self):
        self.assertEqual(SoftPointProbe.objects.count(), 0)
        for bad in ("39.99", "0", "120.01", "999"):
            with self.assertRaises(ValidationError):
                self._probe(bad).save()
        # 任何越界写都不得落库
        self.assertEqual(SoftPointProbe.objects.count(), 0)

    def test_boundary_values_persist(self):
        self._probe("40").save()
        self._probe("120").save()
        self.assertEqual(SoftPointProbe.objects.count(), 2)

    def test_out_of_range_update_rolls_back_keeps_old_value(self):
        probe = self._probe("88.50")
        probe.save()
        probe.softPointC = Decimal("200")
        with self.assertRaises(ValidationError):
            probe.save()
        probe.refresh_from_db()
        self.assertEqual(probe.softPointC, Decimal("88.50"))

    def test_database_check_constraint_blocks_raw_out_of_range(self):
        # 绕过 ORM 直接写 SQL：库层 CHECK 约束是最后一道防线。
        with connection.cursor() as c:
            with self.assertRaises(IntegrityError):
                c.execute(
                    "INSERT INTO kiln_softpointprobe "
                    "(run_id, sampledAt, softPointC, samplerName) "
                    "VALUES (%s, %s, %s, %s)",
                    [self.run.id, "2026-01-01 00:00:00", "999", "raw"],
                )
        self.assertEqual(SoftPointProbe.objects.count(), 0)


class AddProbeViewTests(TestCase):
    def setUp(self):
        self.hearth, self.run = make_open_run()
        self.user = get_user_model().objects.create_user("tester", password="x")
        self.client = Client()
        self.client.force_login(self.user)
        self.url = f"/hearth/{self.hearth.pk}/probe/"
        self.now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")

    def _post(self, soft_point="88.50", sampler="测试员", htmx=True, **extra):
        data = {"sampledAt": self.now, "softPointC": soft_point,
                "samplerName": sampler}
        data.update(extra)
        headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        return self.client.post(self.url, data, **headers)

    def test_valid_create_persists_and_appears(self):
        resp = self._post("88.50")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SoftPointProbe.objects.count(), 1)
        probe = SoftPointProbe.objects.get()
        self.assertEqual(probe.softPointC, Decimal("88.50"))
        html = resp.content.decode()
        # 真实读数出现在时间线，且无内联字段错误
        self.assertIn("88.50℃", timeline_section(html))
        self.assertNotIn("errorlist", html)

    def test_out_of_range_create_leaves_zero_rows(self):
        for bad in ("200", "10"):
            resp = self._post(bad)
            self.assertEqual(resp.status_code, 200)
        # 失败零残行
        self.assertEqual(SoftPointProbe.objects.count(), 0)

    def test_out_of_range_timeline_matches_db_and_hides_phantom(self):
        # 用户场景：时间线先冒一条、抽屉才提示失败。
        resp = self._post("200.00")
        html = resp.content.decode()
        timeline = timeline_section(html)
        # 库里没有这条越界残行
        self.assertEqual(SoftPointProbe.objects.count(), 0)
        # 时间线渲染的真实条数 == 库内条数（都是 0，empty 提示）
        self.assertEqual(timeline_row_count(html), SoftPointProbe.objects.count())
        # 时间线区域绝不显示这条幻影读数（登记表单回显输入是另一回事）
        self.assertNotIn("200.00", timeline)
        # 失败提示直接出现在抽屉里（字段级内联错误），而非先冒时间线条
        self.assertIn("errorlist", html)
        self.assertIn("40～120", html)

    def test_non_numeric_value_leaves_zero_rows(self):
        # 旧逻辑在表单失败分支会把原始值/0 直接 save()；此处必须零写入。
        resp = self._post("abc")
        self.assertEqual(SoftPointProbe.objects.count(), 0)
        self.assertEqual(timeline_row_count(resp.content.decode()), 0)

    def test_missing_sampler_leaves_zero_rows(self):
        resp = self._post(sampler="")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SoftPointProbe.objects.count(), 0)

    def test_invalid_then_valid_timeline_count_equals_db_count(self):
        self._post("200")  # 失败，零残行
        self._post("10")   # 失败，零残行
        self._post("90.00")  # 成功
        self._post("91.00")  # 成功
        resp = self._post("92.00")  # 成功
        db_count = SoftPointProbe.objects.count()
        self.assertEqual(db_count, 3)
        self.assertEqual(timeline_row_count(resp.content.decode()), db_count)

    def test_non_htmx_out_of_range_no_rows(self):
        resp = self._post("200", htmx=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SoftPointProbe.objects.count(), 0)


class EditProbeViewTests(TestCase):
    def setUp(self):
        self.hearth, self.run = make_open_run()
        self.user = get_user_model().objects.create_user("tester2", password="x")
        self.client = Client()
        self.client.force_login(self.user)
        self.probe = SoftPointProbe.objects.create(
            run=self.run,
            sampledAt=timezone.now(),
            softPointC=Decimal("88.00"),
            samplerName="原测试员",
        )
        self.url = f"/probes/{self.probe.pk}/edit/"

    def _post(self, soft_point="88.00", sampler="原测试员"):
        return self.client.post(self.url, {
            "sampledAt": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "softPointC": soft_point,
            "samplerName": sampler,
        })

    def test_valid_update_persists(self):
        resp = self._post("90.00", "新测试员")
        self.assertEqual(resp.status_code, 302)
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.softPointC, Decimal("90.00"))
        self.assertEqual(self.probe.samplerName, "新测试员")
        self.assertEqual(SoftPointProbe.objects.count(), 1)

    def test_out_of_range_update_keeps_original(self):
        resp = self._post("200.00")
        self.assertEqual(resp.status_code, 200)  # 回显编辑页
        self.probe.refresh_from_db()
        # 原值原封不动，库里没有越界值，也没有新增行
        self.assertEqual(self.probe.softPointC, Decimal("88.00"))
        self.assertEqual(SoftPointProbe.objects.count(), 1)
        self.assertEqual(SoftPointProbe.objects.get().softPointC, Decimal("88.00"))

    def test_below_range_update_keeps_original(self):
        resp = self._post("5")
        self.assertEqual(resp.status_code, 200)
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.softPointC, Decimal("88.00"))

    def test_invalid_form_update_keeps_original(self):
        resp = self._post(sampler="")
        self.assertEqual(resp.status_code, 200)
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.samplerName, "原测试员")
        self.assertEqual(SoftPointProbe.objects.count(), 1)


class TimelineHonestyTests(TestCase):
    """展示层不得把库内真实读数夹成表面合规的数字。"""

    def setUp(self):
        self.hearth, self.run = make_open_run(tag="灶-乙", lane=2)
        self.user = get_user_model().objects.create_user("tester3", password="x")
        self.client = Client()
        self.client.force_login(self.user)

    def test_drawer_shows_true_high_value_not_clamped_to_95(self):
        SoftPointProbe.objects.create(
            run=self.run,
            sampledAt=timezone.now(),
            softPointC=Decimal("102.40"),  # 合法(≤120)但 >95，未达出胶标准
            samplerName="周磊",
        )
        resp = self.client.get(
            f"/hearth/{self.hearth.pk}/drawer/",
            HTTP_HX_REQUEST="true",
        )
        html = resp.content.decode()
        self.assertIn("102.40", html)        # 显示真实值
        self.assertIn('class="hot"', html)   # 用 .hot 诚实标注超标
        # 不得把 102.40 伪装成 95：时间线区域不出现 95℃
        self.assertNotIn(">95℃<", timeline_section(html).replace(" ", ""))

    def test_timeline_row_count_equals_real_db_count(self):
        values = ["102.40", "96.20", "93.50", "88.00"]
        for v in values:
            SoftPointProbe.objects.create(
                run=self.run,
                sampledAt=timezone.now(),
                softPointC=Decimal(v),
                samplerName="测试员",
            )
        resp = self.client.get(
            f"/hearth/{self.hearth.pk}/drawer/",
            HTTP_HX_REQUEST="true",
        )
        html = resp.content.decode()
        # 验收标准：时间线条数 == 真实入库条数
        self.assertEqual(timeline_row_count(html), SoftPointProbe.objects.count())
        self.assertEqual(timeline_row_count(html), 4)
        for v in values:
            self.assertIn(v, html)
