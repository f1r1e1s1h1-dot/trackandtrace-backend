from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Q

from .models import (
    RollMaster,
    JobUsage,
    Dispatch,
    DeliveryConfirmation,
    QRLink,
)

# ========================================================
# QR LINKING UTILITIES
# ========================================================

def resolve_root_qr(qr_value: str) -> str:
    if not qr_value:
        return qr_value

    visited = set()
    current = qr_value

    while current and current not in visited:
        visited.add(current)
        link = QRLink.objects.filter(
            current_qr_value=current
        ).order_by("-id").first()
        if not link:
            break
        current = link.previous_qr_value

    return current


def get_roll_by_any_qr(qr_value: str):
    root_qr = resolve_root_qr(qr_value)
    roll = RollMaster.objects.filter(qr_value=root_qr).first()
    return roll, root_qr


def maybe_link_previous(previous_qr: str, current_qr: str):
    if not previous_qr or not current_qr or previous_qr == current_qr:
        return

    QRLink.objects.get_or_create(
        previous_qr_value=previous_qr,
        current_qr_value=current_qr,
    )


def get_all_qrs_from_root(root_qr: str):
    seen = {root_qr}
    frontier = {root_qr}

    while frontier:
        links = QRLink.objects.filter(
            previous_qr_value__in=frontier
        ).values_list("current_qr_value", flat=True)

        new = set(links) - seen
        seen |= new
        frontier = new

    return seen


# ========================================================
# RESPONSE FORMATTERS
# ========================================================

def phase_obj(time_val, data):
    if not time_val:
        return {"completed": False, "time": None, "data": None}
    return {"completed": True, "time": time_val, "data": data or {}}


def build_roll_process_response(roll: RollMaster, qr_used: str):
    root_qr = roll.qr_value
    all_qrs = get_all_qrs_from_root(root_qr)

    job_first = JobUsage.objects.filter(roll=roll).order_by("id").first()
    job_last = JobUsage.objects.filter(roll=roll).order_by("-id").first()
    dispatch = Dispatch.objects.filter(qr_value__in=all_qrs).order_by("-id").first()
    delivery = DeliveryConfirmation.objects.filter(qr_value__in=all_qrs).order_by("-id").first()

    inward_data = {
        "roll_id": roll.roll_id,
        "supplier": roll.supplier,
        "gsm": roll.gsm,
        "width": roll.width,
        "lot_no": roll.lot_no,
        "status": roll.status,
    }

    flexo_start_data = None
    flexo_start_time = None
    if job_first:
        flexo_start_time = job_first.start_time
        flexo_start_data = {
            "operator_name": job_first.operator_name,
            "job_id": job_first.job_id,
            "machine": job_first.machine,
            "remarks": job_first.remarks,
        }

    flexo_end_data = None
    flexo_end_time = None
    if job_last and job_last.end_time:
        flexo_end_time = job_last.end_time
        flexo_end_data = {
            "operator_name": job_last.operator_name,
            "meters_printed": job_last.meters_printed,
            "wastage": job_last.wastage,
            "remarks": job_last.remarks,
        }

    dispatch_data = None
    dispatch_time = None
    if dispatch:
        dispatch_time = dispatch.created_at
        dispatch_data = {
            "customer": dispatch.customer,
            "dispatch_qty": dispatch.dispatch_qty,
            "vehicle_no": dispatch.vehicle_no,
            "invoice_no": dispatch.invoice_no,
        }

    delivery_data = None
    delivery_time = None
    if delivery:
        delivery_time = delivery.created_at
        delivery_data = {
            "operator_name": delivery.operator_name,
            "remarks": delivery.remarks,
        }

    return {
        "qr_value": qr_used,
        "root_qr_value": root_qr,
        "roll_id": roll.roll_id,
        "status": roll.status,
        "phases": {
            "inward": phase_obj(roll.created_at, inward_data),
            "flexo_start": phase_obj(flexo_start_time, flexo_start_data),
            "flexo_end": phase_obj(flexo_end_time, flexo_end_data),
            "dispatch": phase_obj(dispatch_time, dispatch_data),
            "delivery": phase_obj(delivery_time, delivery_data),
        },
        "timeline": {
            "inward": roll.created_at,
            "print_start": flexo_start_time,
            "print_end": flexo_end_time,
            "dispatch": dispatch_time,
            "delivered": delivery_time,
        },
    }


# ========================================================
# APIs
# ========================================================

@api_view(["POST"])
def scan_roll(request):
    qr_value = (request.data.get("qr_value") or "").strip()
    if not qr_value:
        return Response({"error": "qr_value required"}, status=400)

    roll, root_qr = get_roll_by_any_qr(qr_value)
    if not roll:
        return Response({"found": False}, status=404)

    return Response({
        "found": True,
        "root_qr_value": root_qr,
        "roll": {
            "roll_id": roll.roll_id,
            "qr_value": roll.qr_value,
            "supplier": roll.supplier,
            "gsm": roll.gsm,
            "width": roll.width,
            "lot_no": roll.lot_no,
            "status": roll.status,
        }
    })


@api_view(["POST"])
def inward(request):
    data = request.data
    qr_value = (data.get("qr_value") or "").strip()
    roll_id = (data.get("roll_id") or "").strip()

    if not qr_value or not roll_id:
        return Response({"error": "qr_value and roll_id required"}, status=400)

    roll, _ = RollMaster.objects.get_or_create(
        qr_value=qr_value,
        defaults={"roll_id": roll_id}
    )

    roll.roll_id = roll_id
    roll.supplier = data.get("supplier", "")
    roll.gsm = data.get("gsm") or 0
    roll.width = data.get("width") or 0
    roll.lot_no = data.get("lot_no", "")
    roll.status = "IN_STOCK"
    roll.save()

    return Response({"status": "INWARD_SAVED"})


@api_view(["POST"])
def flexo_start(request):
    data = request.data
    current_qr = (data.get("qr_value") or "").strip()
    previous_qr = (data.get("previous_qr_value") or "").strip()

    if not current_qr:
        return Response({"error": "qr_value required"}, status=400)

    maybe_link_previous(previous_qr, current_qr)

    roll, _ = get_roll_by_any_qr(current_qr)
    if not roll:
        return Response({"error": "Roll not found"}, status=404)

    JobUsage.objects.create(
        roll=roll,
        job_id=data.get("job_id") or data.get("job_name"),
        machine=data.get("machine") or data.get("machine_no"),
        operator_name=data.get("operator_name"),
        remarks=data.get("remarks"),
        start_time=timezone.now(),
    )

    roll.status = "IN_USE"
    roll.save()

    return Response({"status": "PRINTING_STARTED"})


@api_view(["POST"])
def flexo_end(request):
    data = request.data
    current_qr = (data.get("qr_value") or "").strip()

    roll, _ = get_roll_by_any_qr(current_qr)
    if not roll:
        return Response({"error": "Roll not found"}, status=404)

    job = JobUsage.objects.filter(roll=roll).order_by("-id").first()
    if job:
        job.meters_printed = data.get("meters_printed") or 0
        job.wastage = data.get("wastage") or 0
        job.end_time = timezone.now()
        job.save()

    roll.status = "PRINTING_COMPLETED"
    roll.save()

    return Response({"status": "PRINTING_COMPLETED"})


@api_view(["GET"])
def timeline(request):
    qr = (request.query_params.get("qr") or "").strip()
    roll, _ = get_roll_by_any_qr(qr)
    if not roll:
        return Response({"error": "QR not found"}, status=404)

    return Response(build_roll_process_response(roll, qr))


# ========================================================
# ADMIN APIs
# ========================================================

@api_view(["GET"])
def admin_summary(request):
    return Response({
        "counts": {
            "TOTAL": RollMaster.objects.count(),
            "IN_STOCK": RollMaster.objects.filter(status="IN_STOCK").count(),
            "IN_USE": RollMaster.objects.filter(status="IN_USE").count(),
            "PRINTING_COMPLETED": RollMaster.objects.filter(status="PRINTING_COMPLETED").count(),
            "DISPATCHED": RollMaster.objects.filter(status="DISPATCHED").count(),
            "DELIVERED": RollMaster.objects.filter(status="DELIVERED").count(),
        }
    })


@api_view(["GET"])
def admin_search(request):
    q = (request.query_params.get("q") or "").strip()
    rolls = RollMaster.objects.filter(
        Q(roll_id__icontains=q) |
        Q(qr_value__icontains=q)
    )[:50]

    return Response({
        "count": rolls.count(),
        "results": [build_roll_process_response(r, r.qr_value) for r in rolls]
    })
