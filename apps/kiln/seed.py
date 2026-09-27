from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe


def ensure_seed_data():
    """幂等种子：账号 + 来脂批 / 灶台 / 值守 / 探针。"""
    User = get_user_model()

    if not User.objects.filter(username="admin").exists():
        User.objects.create_superuser("admin", "admin@pitchkiln.local", "123456")

    if not User.objects.filter(username="worker").exists():
        User.objects.create_user("worker", "worker@pitchkiln.local", "123456")

    if FireHearth.objects.exists():
        return

    now = timezone.now()

    lot_a = ResinLot.objects.create(
        lotCode="脂-松脂坳-2409A",
        originPlace="松脂坳东沟",
        arrivalKg=Decimal("1860.00"),
        receivedAt=now - timezone.timedelta(days=2),
    )
    lot_b = ResinLot.objects.create(
        lotCode="脂-桐油坑-2409B",
        originPlace="桐油坑北坡",
        arrivalKg=Decimal("1420.50"),
        receivedAt=now - timezone.timedelta(days=1, hours=6),
    )
    lot_c = ResinLot.objects.create(
        lotCode="脂-松脂坳-2409C",
        originPlace="松脂坳西岔",
        arrivalKg=Decimal("980.00"),
        receivedAt=now - timezone.timedelta(hours=10),
    )

    h1 = FireHearth.objects.create(
        lane=1,
        tag="坳火-甲",
        resinGrade="特级脂",
        phase=FireHearth.PHASE_HOLDING,
    )
    h2 = FireHearth.objects.create(
        lane=1,
        tag="坳火-乙",
        resinGrade="一级脂",
        phase=FireHearth.PHASE_RAMPING,
    )
    h3 = FireHearth.objects.create(
        lane=2,
        tag="坑火-西一",
        resinGrade="特级脂",
        phase=FireHearth.PHASE_DRAWING,
    )
    FireHearth.objects.create(
        lane=2,
        tag="坑火-西二",
        resinGrade="二级脂",
        phase=FireHearth.PHASE_COLD,
    )
    h5 = FireHearth.objects.create(
        lane=3,
        tag="坳火-夜班",
        resinGrade="浮油级",
        phase=FireHearth.PHASE_CHARGING,
    )

    run1 = CookRun.objects.create(
        hearth=h1,
        resinLot=lot_a,
        openedAt=now - timezone.timedelta(hours=8),
        closedAt=None,
        targetSoftPointC=Decimal("88.00"),
    )
    SoftPointProbe.objects.create(
        run=run1,
        sampledAt=now - timezone.timedelta(hours=3),
        softPointC=Decimal("102.40"),
        samplerName="值守周磊",
    )
    SoftPointProbe.objects.create(
        run=run1,
        sampledAt=now - timezone.timedelta(hours=1),
        softPointC=Decimal("96.20"),
        samplerName="值守周磊",
    )

    run2 = CookRun.objects.create(
        hearth=h2,
        resinLot=lot_c,
        openedAt=now - timezone.timedelta(hours=4),
        closedAt=None,
        targetSoftPointC=Decimal("90.00"),
    )
    SoftPointProbe.objects.create(
        run=run2,
        sampledAt=now - timezone.timedelta(hours=1, minutes=20),
        softPointC=Decimal("108.00"),
        samplerName="值守阿坤",
    )

    run3 = CookRun.objects.create(
        hearth=h3,
        resinLot=lot_b,
        openedAt=now - timezone.timedelta(hours=14),
        closedAt=None,
        targetSoftPointC=Decimal("86.00"),
    )
    SoftPointProbe.objects.create(
        run=run3,
        sampledAt=now - timezone.timedelta(hours=6),
        softPointC=Decimal("99.10"),
        samplerName="值守阿萍",
    )
    SoftPointProbe.objects.create(
        run=run3,
        sampledAt=now - timezone.timedelta(hours=2),
        softPointC=Decimal("93.50"),
        samplerName="值守阿萍",
    )

    CookRun.objects.create(
        hearth=h5,
        resinLot=lot_a,
        openedAt=now - timezone.timedelta(minutes=40),
        closedAt=None,
        targetSoftPointC=Decimal("87.00"),
    )
