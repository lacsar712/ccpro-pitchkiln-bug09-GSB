# Generated manually for PitchKiln-01 floor board domain

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ResinLot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "lotCode",
                    models.CharField(
                        max_length=64, unique=True, verbose_name="来脂批号"
                    ),
                ),
                (
                    "originPlace",
                    models.CharField(max_length=120, verbose_name="来源地"),
                ),
                (
                    "arrivalKg",
                    models.DecimalField(
                        decimal_places=2, max_digits=10, verbose_name="到货量(kg)"
                    ),
                ),
                ("receivedAt", models.DateTimeField(verbose_name="到货时间")),
            ],
            options={
                "verbose_name": "来脂批",
                "verbose_name_plural": "来脂批",
                "ordering": ["-receivedAt", "-id"],
            },
        ),
        migrations.CreateModel(
            name="FireHearth",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("lane", models.PositiveIntegerField(verbose_name="过道号")),
                (
                    "tag",
                    models.CharField(max_length=40, unique=True, verbose_name="灶牌"),
                ),
                (
                    "resinGrade",
                    models.CharField(max_length=80, verbose_name="松香品级标签"),
                ),
                (
                    "phase",
                    models.CharField(
                        choices=[
                            ("cold", "冷灶"),
                            ("charging", "装料"),
                            ("ramping", "升温"),
                            ("holding", "保温"),
                            ("drawing", "出胶"),
                        ],
                        default="cold",
                        max_length=20,
                        verbose_name="相位",
                    ),
                ),
            ],
            options={
                "verbose_name": "灶台",
                "verbose_name_plural": "灶台",
                "ordering": ["lane", "tag"],
            },
        ),
        migrations.CreateModel(
            name="CookRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("openedAt", models.DateTimeField(verbose_name="开灶时间")),
                (
                    "closedAt",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="收灶时间"
                    ),
                ),
                (
                    "targetSoftPointC",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=6,
                        verbose_name="目标软化点(℃)",
                    ),
                ),
                (
                    "hearth",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="runs",
                        to="kiln.firehearth",
                        verbose_name="灶台",
                    ),
                ),
                (
                    "resinLot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="runs",
                        to="kiln.resinlot",
                        verbose_name="来脂批",
                    ),
                ),
            ],
            options={
                "verbose_name": "熬制值守",
                "verbose_name_plural": "熬制值守",
                "ordering": ["-openedAt", "-id"],
            },
        ),
        migrations.CreateModel(
            name="SoftPointProbe",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("sampledAt", models.DateTimeField(verbose_name="取样时间")),
                (
                    "softPointC",
                    models.DecimalField(
                        decimal_places=2, max_digits=6, verbose_name="软化点(℃)"
                    ),
                ),
                (
                    "samplerName",
                    models.CharField(max_length=80, verbose_name="取样人"),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="probes",
                        to="kiln.cookrun",
                        verbose_name="值守",
                    ),
                ),
            ],
            options={
                "verbose_name": "软化点探针",
                "verbose_name_plural": "软化点探针",
                "ordering": ["-sampledAt", "-id"],
            },
        ),
    ]
