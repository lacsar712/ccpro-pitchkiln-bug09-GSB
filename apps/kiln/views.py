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


def _drawer_context(hearth, probe_form=None, standalone=False):
    open_run = hearth.open_run()
    probes = []
    if open_run:
        probes = list(open_run.probes.order_by("-sampledAt", "-id"))
    return {
        "hearth": hearth,
        "open_run": open_run,
        "probes": probes,
        "phase_form": PhaseChangeForm(hearth=hearth),
        # 校验失败时回传绑定表单（带字段错误），成功/初次打开给空白表单
        "probe_form": probe_form if probe_form is not None
        else (SoftPointProbeForm() if open_run else None),
        "open_run_form": OpenCookRunForm(hearth=hearth) if open_run is None else None,
        # HTMX 局部刷新时消息不在 base 页框里，需在抽屉内自行展示
        "drawer_standalone": standalone,
    }


def _probe_save_error(exc):
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            for msgs in exc.message_dict.values():
                if msgs:
                    return str(msgs[0])
        if getattr(exc, "messages", None):
            return str(exc.messages[0])
    return "探针登记失败：软化点超出允许范围（40～120℃）"


def _form_errors_to_messages(request, form):
    for field, errs in form.errors.items():
        label = form.fields[field].label if field in form.fields else None
        messages.error(request, f"{label or '输入'}：{errs[0]}")


def _drawer_response(request, hearth, probe_form=None):
    resp = render(
        request, "floor/_drawer.html",
        _drawer_context(hearth, probe_form=probe_form, standalone=True),
    )
    resp["HX-Trigger"] = "floor-refresh"
    return resp


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
    if _wants_htmx(request):
        return render(
            request, "floor/_drawer.html",
            _drawer_context(hearth, standalone=True),
        )
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
        return _drawer_response(request, hearth)
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def add_probe(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守，无法登记探针")
        if _wants_htmx(request):
            return _drawer_response(request, hearth)
        return redirect(f"/?hearth={pk}")

    form = SoftPointProbeForm(request.POST)
    if form.is_valid():
        probe = form.save(commit=False)
        probe.run = open_run
        try:
            with transaction.atomic():
                # 模型层先校验后写库；任何越界/完整性错误整体回滚，不留残行
                probe.save()
        except (ValidationError, IntegrityError) as exc:
            form.add_error("softPointC", _probe_save_error(exc))
        else:
            messages.success(request, f"已登记探针 {probe.softPointC}℃")

    if not form.is_valid():
        _form_errors_to_messages(request, form)

    if _wants_htmx(request):
        # 失败时把绑定表单带回抽屉，错误内联展示且绝不新增时间线残行
        return _drawer_response(
            request, hearth, probe_form=form if not form.is_valid() else None
        )
    return redirect(f"/?hearth={pk}")


@login_required
@require_http_methods(["GET", "POST"])
def edit_probe(request, pk):
    probe = get_object_or_404(SoftPointProbe, pk=pk)
    hearth = probe.run.hearth
    if request.method == "POST":
        form = SoftPointProbeForm(request.POST, instance=probe)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # 先校验后写库：越界时 full_clean 拦截，UPDATE 不会发出，
                    # 库里原值保持不变，时间线仍只映真实入库读数。
                    form.save()
            except (ValidationError, IntegrityError) as exc:
                form.add_error("softPointC", _probe_save_error(exc))
            else:
                messages.success(request, "探针已更新")

        if not form.is_valid():
            _form_errors_to_messages(request, form)

        if _wants_htmx(request):
            return _drawer_response(
                request, hearth, probe_form=form if not form.is_valid() else None
            )
        if not form.is_valid():
            # 非 HTMX 提交失败：留在编辑页，表单带错误、显示用户输入，库内原值未动
            return render(
                request,
                "floor/probe_edit.html",
                {"form": form, "probe": probe, "hearth": hearth},
            )
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
        return _drawer_response(request, hearth)
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
