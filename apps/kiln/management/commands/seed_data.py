from django.core.management.base import BaseCommand

from apps.kiln.seed import ensure_seed_data


class Command(BaseCommand):
    help = "写入演示账号与灶台值守样例数据（幂等）"

    def handle(self, *args, **options):
        ensure_seed_data()
        self.stdout.write(self.style.SUCCESS("种子数据已就绪（admin / worker）"))
