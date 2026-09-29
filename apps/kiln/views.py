from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import OpenCookRunForm, PhaseChangeForm, ResinLotForm, SoftPointProbeForm

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe
from .services.floor_rules import change_hearth_phase


def _wants_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _first_form_error(form, fallback="提交失败，请检查输入"):
    """取表单第一条可展示错误，不夹带库内原始值。"""
    for name, errs in form.errors.items():
        if not errs:
            continue
        if name == "__all__":
            return errs[0]
        field = form.fields.get(name)
        label = field.label if field and field.label else name
        return f"{label}：{errs[0]}"
    return fallback


def _validation_message(exc, fallback):
    """把模型层 ValidationError 转成第一条人类可读信息；其余异常给兜底文案。"""
    if isinstance(exc, ValidationError):
        if exc.message_dict:
            first = next(iter(exc.message_dict.values()), None)
            if first:
                return first[0]
        if exc.messages:
            return exc.messages[0]
    return fallback


def _render_drawer(request, hearth, **ctx_kwargs):
    hearth.refresh_from_db()
    resp = render(
        request, "floor/_drawer.html", _drawer_context(hearth, **ctx_kwargs)
    )
    resp["HX-Trigger"] = "floor-refresh"
    return resp



def _hearths_for_board():
    return FireHearth.objects.prefetch_related(
        Prefetch(
            "runs",
            queryset=CookRun.objects.filter(closedAt__isnull=True)
            .select_related("resinLot")
            .prefetch_related("probes"),
            to_attr="open_runs_cache",
        )
    ).order_by("lane", "tag")


def _board_context():
    hearths = list(_hearths_for_board())
    lanes = {}
    for h in hearths:
        lanes.setdefault(h.lane, []).append(h)
    phase_legend = [
        (key, label, sum(1 for h in hearths if h.phase == key))
        for key, label in FireHearth.PHASE_CHOICES
    ]
    return {
        "hearths": hearths,
        "lanes": sorted(lanes.items()),
        "phase_legend": phase_legend,
    }


def _drawer_context(hearth, probe_form=None, open_run_form=None, phase_form=None):
    open_run = hearth.open_run()
    probes = []
    if open_run:
        probes = list(open_run.probes.order_by("-sampledAt", "-id"))
    return {
        "hearth": hearth,
        "open_run": open_run,
        "probes": probes,
        "phase_form": phase_form or PhaseChangeForm(hearth=hearth),
        # 校验失败时回传绑定表单（带内联错误与用户原始输入），
        # 而不是新建空表单掩盖失败，也绝不产生残行。
        "probe_form": (
            probe_form if probe_form is not None
            else (SoftPointProbeForm() if open_run else None)
        ),
        "open_run_form": (
            open_run_form if open_run_form is not None
            else (OpenCookRunForm(hearth=hearth) if open_run is None else None)
        ),
    }


@login_required
def home(request):
    ctx = _board_context()
    drawer_pk = request.GET.get("hearth")
    if drawer_pk:
        try:
            hearth = FireHearth.objects.get(pk=drawer_pk)
            ctx.update(_drawer_context(hearth))
            ctx["drawer_open"] = True
        except (FireHearth.DoesNotExist, ValueError):
            ctx["drawer_open"] = False
    else:
        ctx["drawer_open"] = False
    return render(request, "floor/board.html", ctx)


@login_required
def floor_grid_partial(request):
    html = render_to_string("floor/_grid.html", _board_context(), request=request)
    return HttpResponse(html)


@login_required
def hearth_drawer(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    ctx = _drawer_context(hearth)
    if _wants_htmx(request):
        return render(request, "floor/_drawer.html", ctx)
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def change_phase(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    form = PhaseChangeForm(request.POST, hearth=hearth)
    if form.is_valid():
        try:
            change_hearth_phase(hearth, form.cleaned_data["phase"])
            messages.success(request, f"灶牌 {hearth.tag} 相位已更新")
        except ValidationError as exc:
            msg = (
                exc.message_dict.get("phase") if hasattr(exc, "message_dict") else None
            )
            messages.error(request, msg[0] if msg else str(exc))
    else:
        err = form.errors.get("phase")
        messages.error(request, err[0] if err else "相位切换失败")

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def add_probe(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守，无法登记探针")
        if _wants_htmx(request):
            return _render_drawer(request, hearth)
        return redirect(f"/?hearth={pk}")

    form = SoftPointProbeForm(request.POST)
    if not form.is_valid():
        # 校验未过：绝不写库，抽屉回显带错误的绑定表单，不留残行。
        messages.error(request, _first_form_error(form, "探针登记失败，请检查输入"))
        if _wants_htmx(request):
            return _render_drawer(request, hearth, probe_form=form)
        return redirect(f"/?hearth={pk}")

    probe = form.save(commit=False)
    probe.run = open_run
    try:
        # 模型 save 先 full_clean 再写库，外层事务保证任何失败整体回滚。
        with transaction.atomic():
            probe.save()
    except ValidationError as exc:
        messages.error(request, _validation_message(exc, "探针校验失败"))
        if _wants_htmx(request):
            return _render_drawer(request, hearth, probe_form=form)
        return redirect(f"/?hearth={pk}")
    except IntegrityError:
        messages.error(request, "探针校验失败：软化点超出允许范围（40～120℃）")
        if _wants_htmx(request):
            return _render_drawer(request, hearth, probe_form=form)
        return redirect(f"/?hearth={pk}")

    messages.success(request, f"已登记探针 {probe.softPointC}℃")
    if _wants_htmx(request):
        return _render_drawer(request, hearth)
    return redirect(f"/?hearth={pk}")


@login_required
@require_http_methods(["GET", "POST"])
def edit_probe(request, pk):
    probe = get_object_or_404(SoftPointProbe, pk=pk)
    hearth = probe.run.hearth
    if request.method == "POST":
        form = SoftPointProbeForm(request.POST, instance=probe)
        if not form.is_valid():
            # 更新校验失败：不写库，整页回显带错误的表单，原行保持不变。
            messages.error(request, _first_form_error(form, "探针更新失败，请检查输入"))
            return render(
                request,
                "floor/probe_edit.html",
                {"form": form, "probe": probe, "hearth": hearth},
            )

        obj = form.save(commit=False)
        try:
            with transaction.atomic():
                obj.save()
        except ValidationError as exc:
            messages.error(request, _validation_message(exc, "探针更新校验失败"))
            return render(
                request,
                "floor/probe_edit.html",
                {"form": form, "probe": probe, "hearth": hearth},
            )
        except IntegrityError:
            messages.error(request, "探针更新失败：软化点超出允许范围（40～120℃）")
            return render(
                request,
                "floor/probe_edit.html",
                {"form": form, "probe": probe, "hearth": hearth},
            )

        messages.success(request, "探针已更新")
        return redirect(f"/?hearth={hearth.pk}")

    form = SoftPointProbeForm(instance=probe)
    return render(
        request,
        "floor/probe_edit.html",
        {"form": form, "probe": probe, "hearth": hearth},
    )


@login_required
@require_POST
def open_run(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    form = OpenCookRunForm(request.POST, hearth=hearth)
    if form.is_valid():
        run = form.save(commit=False)
        run.hearth = hearth
        run.save()
        if hearth.phase == FireHearth.PHASE_COLD:
            hearth.phase = FireHearth.PHASE_CHARGING
            hearth.save(update_fields=["phase"])
        messages.success(request, "新值守已开灶")
    else:
        for errs in form.errors.values():
            for e in errs:
                messages.error(request, e)
            break

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def close_run(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守可收灶")
    else:
        open_run.closedAt = timezone.now()
        open_run.save(update_fields=["closedAt"])
        hearth.phase = FireHearth.PHASE_COLD
        hearth.save(update_fields=["phase"])
        messages.success(request, "值守已收灶，灶台回冷灶")

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_http_methods(["GET", "POST"])
def resin_lot_feed(request):
    if request.method == "POST":
        form = ResinLotForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "来脂批已登记")
            return redirect("resin_lot_feed")
    else:
        form = ResinLotForm(
            initial={
                "receivedAt": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            }
        )

    lots = ResinLot.objects.all()[:40]
    return render(request, "resin/feed.html", {"lots": lots, "form": form})
