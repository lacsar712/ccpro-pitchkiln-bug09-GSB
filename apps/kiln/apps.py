from django.apps import AppConfig


class KilnConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.kiln"
    verbose_name = "松香熬制窑"

    def ready(self):
        import os

        if os.environ.get("PITCHKILN_AUTO_SEED") == "1":
            from django.db.models.signals import post_migrate

            def _seed(sender, **kwargs):
                from apps.kiln.seed import ensure_seed_data

                ensure_seed_data()

            post_migrate.connect(_seed, sender=self)
