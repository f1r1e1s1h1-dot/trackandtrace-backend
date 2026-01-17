from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q
import requests

from .models import RollMaster, JobUsage, Dispatch, DeliveryConfirmation, QRLink


# ========================================================
# GPS → CITY USING OPENSTREETMAP (SAFE: timeout + fallback)
# ========================================================
def get_city_from_gps(lat, lng):
    """
    Render free tier can sometimes fail outbound calls or OSM can rate-limit.
    We must NEVER crash the API due to this.
    """
    try:
        if lat is None or lng is None:
            return "Unknown Location"

        url = (
            "https://nominatim.openstreetmap.org/reverse"
            f"?lat={lat}&lon={lng}&format=json"
        )
        r = requests.get(
            url,
            headers={"User-Agent": "TrackAndTraceApp"},
            timeout=3,   # ✅ important
        )
        if r.status_code != 200:
            return "Unknown Location"

        data = r.json()
        return (
            data.get("address", {}).get("city")
            or data.get("address", {}).get("town")
            or data.get("address", {}).get("village")
            or "Unknown Location"
        )
    except Exception:
        return "Unknown Location"


# ========================================================
# QR LINKING UTILITIES
# ========================================================
def resolve_root_qr(qr_value: str) -> str:
    """
    Walk backwards using QRLink to find RollMaster.qr_value (root QR).
    """
    if not qr_value:
        return qr_value

    current = qr_value
    visited = set()

    while True:
        if current in visited:
            break
        visited.add(current)

        link = (
            QRLink.objects.filter(current_qr_value=current)
            .order_by("-id")
            .first()
        )
        if not link:
            break

        current = link.previous_qr_value

    return current


def get_roll_by_any_qr(qr_value: str):
    root_qr = resolve_root_qr(qr_value)
    roll = RollMaster.objects.filter(qr_value=root_qr).first()
    return roll, root_qr


def maybe_link_previous(previous_qr: str, current_qr: str):
    if not previous_qr or not current_qr:
        return
    if previous_qr == current_qr:
        return

    QRLink.objects.get_or_create(
        previous_qr_value=previous_qr,
        current_qr_value=current_qr,
    )


def get_all_qrs_from_root(root_qr: str):
    """
    Find ALL QRs connected forward from root:
    root -> next -> next ...
    Used to fetch dispatch/delivery even if QR changed.
    """
    if not root_qr:
        return set()

    seen = set([root_qr])
    frontier = set([root_qr])

    while frontier:
        next_frontier = set()
        links = QRLink.objects.filter(
            previous_qr_value__in=list(frontier)
        ).values("previous_qr_value", "current_qr_value")

        for l in links:
            c = l["current_qr_value"]
            if c and c not in seen:
                seen.add(c)
                next_frontier.add(c)

        frontier = next_frontier

    return seen


# ========================================================
# RESPONSE FORMATTERS
# ========================================================
def phase_obj(time_val, data_dict):
    """
    Always return:
    {
      "completed": true/false,
      "time": <time or None>,
      "data": { ... } or None
    }
    """
    if not time_val:
        return {"completed": False, "time": None, "data": None}
    return {"completed": True, "time": time_val, "data": data_dict or {}}


def build_roll_process_response(roll: RollMaster, qr_used: str):
    """
    Builds response for /timeline and admin search.
    """
    root_qr = roll.qr_value
    all_qrs = get_all_qrs_from_root(root_qr)

    job_first = JobUsage.objects.filter(roll=roll).order_by("id").first()
    job_last = JobUsage.objects.filter(roll=roll).order_by("-id").first()

    dispatch_obj = (
        Dispatch.objects.filter(qr_value__in=list(all_qrs))
        .order_by("-id")
        .first()
    )
    delivery_obj = (
        DeliveryConfirmation.objects.filter(qr_value__in=list(all_qrs))
        .order_by("-id")
        .first()
    )

    delivery_city = None
    if (
        delivery_obj
        and getattr(delivery_obj, "gps_lat", None) is not None
        and getattr(delivery_obj, "gps_lng", None) is not None
    ):
        delivery_city = get_city_from_gps(delivery_obj.gps_lat, delivery_obj.gps_lng)

    # Phase: inward
    inward_data = {
        "roll_id": roll.roll_id,
        "qr_value": roll.qr_value,
        "supplier": roll.supplier,
        "gsm": roll.gsm,
        "width": roll.width,
        "lot_no": roll.lot_no,
        "status": roll.status,
    }

    # Phase: flexo_start
    flexo_start_data = None
    flexo_start_time = None
    if job_first:
        flexo_start_time = job_first.start_time
        flexo_start_data = {
            "operator_name": job_first.operator_name,
            "job_id": job_first.job_id,
            "job_name": job_first.job_id,      # alias
            "machine": job_first.machine,
            "machine_no": job_first.machine,   # alias
            "remarks": job_first.remarks,
        }

    # Phase: flexo_end
    flexo_end_data = None
    flexo_end_time = None
    if job_last and job_last.end_time:
        flexo_end_time = job_last.end_time
        flexo_end_data = {
            "operator_name": job_last.operator_name,
            "output_qty": job_last.meters_printed,   # alias
            "meters_printed": job_last.meters_printed,
            "waste_qty": job_last.wastage,           # alias
            "wastage": job_last.wastage,
            "remarks": job_last.remarks,
        }

    # Phase: dispatch
    dispatch_data = None
    dispatch_time = None
    if dispatch_obj:
        dispatch_time = getattr(dispatch_obj, "dispatched_at", None) or getattr(dispatch_obj, "created_at", None)
        dispatch_data = {
            "operator_name": dispatch_obj.operator_name,
            "vehicle_no": dispatch_obj.vehicle_no,
            "dispatch_qty": dispatch_obj.dispatch_qty,
            "customer": dispatch_obj.customer,
            "invoice_no": dispatch_obj.invoice_no,
            "remarks": dispatch_obj.remarks,
            "qr_value": dispatch_obj.qr_value,
        }

    # Phase: delivery
    delivery_data = None
    delivery_time = None
    if delivery_obj:
        delivery_time = getattr(delivery_obj, "confirmed_at", None) or getattr(delivery_obj, "created_at", None)
        delivery_data = {
            "operator_name": delivery_obj.operator_name,
            "remarks": delivery_obj.remarks,
            "gps_lat": getattr(delivery_obj, "gps_lat", None),
            "gps_lng": getattr(delivery_obj, "gps_lng", None),
            "city": delivery_city,
            "qr_value": delivery_obj.qr_value,
            "customer": dispatch_obj.customer if dispatch_obj else None,
        }

    return {
        "qr_value": qr_used,
        "root_qr_value": root_qr,
        "all_known_qrs": sorted(list(all_qrs)),
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
        }
    }


# ========================================================
# APIs
# ========================================================

@api_view(["POST"])
def scan_roll(request):
    qr_value = (request.data.get("qr_value") or "").strip()
    if not qr_value:
        return Response({"found": False, "message": "qr_value missing"}, status=400)

    roll, root_qr = get_roll_by_any_qr(qr_value)
    if roll:
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
                "created_at": roll.created_at
            }
        })

    return Response({"found": False, "message": "QR not found"}, status=404)


@api_view(["POST"])
def inward(request):
    data = request.data
    qr_value = (data.get("qr_value") or "").strip()
    roll_id = (data.get("roll_id") or "").strip()

    if not qr_value:
        return Response({"error": "qr_value is required"}, status=400)
    if not roll_id:
        return Response({"error": "roll_id is required"}, status=400)

    roll = RollMaster.objects.filter(qr_value=qr_value).first()
    if not roll:
        roll = RollMaster.objects.create(
            qr_value=qr_value,
            roll_id=roll_id,
            supplier=data.get("supplier", ""),
            gsm=data.get("gsm") or 0,
            width=data.get("width") or 0,
            lot_no=data.get("lot_no", ""),
            status="IN_STOCK"
        )
    else:
        roll.roll_id = roll_id
        roll.supplier = data.get("supplier", roll.supplier)
        roll.gsm = data.get("gsm", roll.gsm)
        roll.width = data.get("width", roll.width)
        roll.lot_no = data.get("lot_no", roll.lot_no)
        roll.status = "IN_STOCK"
        roll.save()

    return Response({"status": "INWARD_SAVED", "roll_id": roll.roll_id, "qr_value": roll.qr_value})


@api_view(["POST"])
def flexo_start(request):
    data = request.data
    current_qr = (data.get("qr_value") or "").strip()
    previous_qr = (data.get("previous_qr_value") or "").strip()

    if not current_qr:
        return Response({"error": "qr_value is required"}, status=400)

    maybe_link_previous(previous_qr, current_qr)

    roll, root_qr = get_roll_by_any_qr(current_qr)
    if not roll:
        return Response({"error": "Roll not found for this QR"}, status=404)

    JobUsage.objects.create(
        roll=roll,
        job_id=data.get("job_id") or data.get("job_name"),
        machine=data.get("machine") or data.get("machine_no"),
        operator_name=data.get("operator_name"),
        remarks=data.get("remarks"),
        start_time=timezone.now()
    )

    roll.status = "IN_USE"
    roll.save()

    return Response({"status": "PRINTING_STARTED", "root_qr_value": root_qr, "roll_id": roll.roll_id})


@csrf_exempt
@api_view(["POST"])
def flexo_end(request):
    data = request.data
    current_qr = (data.get("qr_value") or "").strip()
    previous_qr = (data.get("previous_qr_value") or "").strip()

    if not current_qr:
        return Response({"error": "qr_value is required"}, status=400)

    maybe_link_previous(previous_qr, current_qr)

    roll, root_qr = get_roll_by_any_qr(current_qr)
    if not roll:
        return Response({"error": "Roll not found for this QR"}, status=404)

    waste = data.get("waste") or data.get("waste_qty") or 0
    meters_printed = data.get("output_qty") or data.get("meters_printed") or None

    job_entry = JobUsage.objects.filter(roll=roll).order_by("-id").first()
    if job_entry:
        job_entry.wastage = waste
        job_entry.meters_printed = meters_printed
        job_entry.end_time = timezone.now()
        job_entry.operator_name = data.get("operator_name") or job_entry.operator_name
        job_entry.remarks = data.get("remarks") or job_entry.remarks
        job_entry.save()

    roll.status = "PRINTING_COMPLETED"
    roll.save()

    return Response({"status": "PRINTING_COMPLETED", "root_qr_value": root_qr, "roll_id": roll.roll_id})


@api_view(["POST"])
def dispatch(request):
    data = request.data
    current_qr = (data.get("qr_value") or "").strip()
    previous_qr = (data.get("previous_qr_value") or "").strip()

    if not current_qr:
        return Response({"error": "qr_value is required"}, status=400)

    maybe_link_previous(previous_qr, current_qr)

    roll, root_qr = get_roll_by_any_qr(current_qr)
    if not roll:
        return Response({"error": "Roll not found for this QR"}, status=404)

    Dispatch.objects.create(
        qr_value=current_qr,
        operator_name=data.get("operator_name"),
        vehicle_no=data.get("vehicle_no"),
        dispatch_qty=data.get("dispatch_qty") or data.get("quantity"),
        remarks=data.get("remarks"),
        customer=data.get("customer"),
        invoice_no=data.get("invoice_no")
    )

    roll.status = "DISPATCHED"
    roll.save()

    return Response({"status": "DISPATCHED", "root_qr_value": root_qr, "roll_id": roll.roll_id})


@api_view(["POST"])
def receiver_scan(request):
    data = request.data
    current_qr = (data.get("qr_value") or "").strip()
    previous_qr = (data.get("previous_qr_value") or "").strip()

    if not current_qr:
        return Response({"error": "qr_value is required"}, status=400)

    maybe_link_previous(previous_qr, current_qr)

    roll, root_qr = get_roll_by_any_qr(current_qr)
    if not roll:
        return Response({"error": "Roll not found for this QR"}, status=404)

    lat = data.get("lat")
    lng = data.get("lng")

    DeliveryConfirmation.objects.create(
        qr_value=current_qr,
        operator_name=data.get("operator_name"),
        remarks=data.get("remarks"),
        gps_lat=lat,
        gps_lng=lng
    )

    roll.status = "DELIVERED"
    roll.save()

    return Response({"status": "DELIVERED", "root_qr_value": root_qr, "roll_id": roll.roll_id})


@api_view(["GET"])
def timeline(request):
    qr = (request.query_params.get("qr") or "").strip()
    if not qr:
        return Response({"error": "qr missing. Use /timeline/?qr=..."}, status=400)

    roll, _ = get_roll_by_any_qr(qr)
    if not roll:
        return Response({"error": "QR not found"}, status=404)

    return Response(build_roll_process_response(roll, qr))


@api_view(["GET"])
def timeline_previous(request):
    prev_qr = (request.query_params.get("qr") or "").strip()
    if not prev_qr:
        return Response({"error": "qr missing. Use /timeline/previous/?qr=..."}, status=400)

    roll, _ = get_roll_by_any_qr(prev_qr)
    if not roll:
        return Response({"error": "QR not found"}, status=404)

    return Response(build_roll_process_response(roll, prev_qr))


# ========================================================
# ADMIN APIs
# ========================================================

def _status_counts():
    return {
        "TOTAL": RollMaster.objects.count(),
        "IN_STOCK": RollMaster.objects.filter(status="IN_STOCK").count(),
        "IN_USE": RollMaster.objects.filter(status="IN_USE").count(),
        "PRINTING_COMPLETED": RollMaster.objects.filter(status="PRINTING_COMPLETED").count(),
        "DISPATCHED": RollMaster.objects.filter(status="DISPATCHED").count(),
        "DELIVERED": RollMaster.objects.filter(status="DELIVERED").count(),
    }


def _distinct_customers():
    return list(
        Dispatch.objects.exclude(customer__isnull=True)
        .exclude(customer__exact="")
        .values_list("customer", flat=True)
        .distinct()
        .order_by("customer")
    )


def _customer_for_delivery_qr(qr_value: str):
    d = Dispatch.objects.filter(qr_value=qr_value).order_by("-id").first()
    if d and d.customer:
        return d.customer

    root = resolve_root_qr(qr_value)
    all_qrs = get_all_qrs_from_root(root)
    d2 = Dispatch.objects.filter(qr_value__in=list(all_qrs)).order_by("-id").first()
    return d2.customer if (d2 and d2.customer) else None


@api_view(["GET"])
def admin_summary(request):
    limit = int(request.query_params.get("limit") or 30)
    customer = (request.query_params.get("customer") or "").strip()

    active_jobs_qs = JobUsage.objects.filter(end_time__isnull=True).order_by("-start_time")[:limit]
    active_printing = []
    for j in active_jobs_qs:
        r = j.roll
        active_printing.append({
            "roll_id": r.roll_id,
            "qr_value": r.qr_value,
            "root_qr_value": r.qr_value,
            "status": r.status,
            "job_id": j.job_id,
            "machine": j.machine,
            "operator_name": j.operator_name,
            "start_time": j.start_time,
        })

    dispatch_qs = Dispatch.objects.order_by("-id")
    if customer:
        dispatch_qs = dispatch_qs.filter(customer__icontains=customer)
    dispatch_qs = dispatch_qs[:limit]

    recent_dispatch = []
    for d in dispatch_qs:
        recent_dispatch.append({
            "qr_value": d.qr_value,
            "customer": d.customer,
            "dispatch_qty": d.dispatch_qty,
            "vehicle_no": d.vehicle_no,
            "invoice_no": d.invoice_no,
            "operator_name": d.operator_name,
            "time": getattr(d, "dispatched_at", None) or getattr(d, "created_at", None),
        })

    delivery_qs = DeliveryConfirmation.objects.order_by("-id")[:limit]
    recent_delivery = []
    for dc in delivery_qs:
        city = None
        if getattr(dc, "gps_lat", None) and getattr(dc, "gps_lng", None):
            city = get_city_from_gps(dc.gps_lat, dc.gps_lng)

        cust = _customer_for_delivery_qr(dc.qr_value)

        if customer and cust and customer.lower() not in cust.lower():
            continue

        recent_delivery.append({
            "qr_value": dc.qr_value,
            "customer": cust,
            "operator_name": dc.operator_name,
            "city": city,
            "time": getattr(dc, "confirmed_at", None) or getattr(dc, "created_at", None),
        })

    return Response({
        "counts": _status_counts(),
        "customers": _distinct_customers(),
        "active_printing": active_printing,
        "recent_dispatch": recent_dispatch,
        "recent_delivery": recent_delivery,
    })


@api_view(["GET"])
def admin_active(request):
    limit = int(request.query_params.get("limit") or 50)
    qs = JobUsage.objects.filter(end_time__isnull=True).order_by("-start_time")[:limit]

    items = []
    for j in qs:
        r = j.roll
        items.append({
            "roll_id": r.roll_id,
            "qr_value": r.qr_value,
            "root_qr_value": r.qr_value,
            "status": r.status,
            "job_id": j.job_id,
            "machine": j.machine,
            "operator_name": j.operator_name,
            "start_time": j.start_time,
        })

    return Response({"count": len(items), "active_printing": items})


@api_view(["GET"])
def admin_search(request):
    q = (request.query_params.get("q") or "").strip()
    customer = (request.query_params.get("customer") or "").strip()
    roll_id = (request.query_params.get("roll_id") or "").strip()
    qr = (request.query_params.get("qr") or "").strip()
    job_id = (request.query_params.get("job_id") or "").strip()
    status = (request.query_params.get("status") or "").strip()
    active_only = (request.query_params.get("active_only") or "").strip().lower() in ("1", "true", "yes")
    limit = int(request.query_params.get("limit") or 50)

    rolls = RollMaster.objects.all()

    if qr:
        roll_obj, _ = get_roll_by_any_qr(qr)
        if not roll_obj:
            return Response({"results": [], "message": "No roll found for this QR", "count": 0})
        return Response({"results": [build_roll_process_response(roll_obj, qr)], "count": 1})

    if roll_id:
        rolls = rolls.filter(roll_id__icontains=roll_id)

    if status:
        rolls = rolls.filter(status__icontains=status)

    if customer:
        dispatch_qrs = Dispatch.objects.filter(customer__icontains=customer).values_list("qr_value", flat=True)
        root_qrs = set(resolve_root_qr(x) for x in dispatch_qrs if x)
        rolls = rolls.filter(qr_value__in=list(root_qrs)) if root_qrs else rolls.none()

    if job_id:
        roll_ids = JobUsage.objects.filter(job_id__icontains=job_id).values_list("roll_id", flat=True)
        rolls = rolls.filter(id__in=list(roll_ids)) if roll_ids else rolls.none()

    if q and not (customer or roll_id or job_id or status):
        rolls_rm = RollMaster.objects.filter(
            Q(roll_id__icontains=q) |
            Q(qr_value__icontains=q) |
            Q(supplier__icontains=q) |
            Q(lot_no__icontains=q) |
            Q(status__icontains=q)
        ).values_list("id", flat=True)

        dispatch_qrs = Dispatch.objects.filter(
            Q(customer__icontains=q) |
            Q(invoice_no__icontains=q) |
            Q(vehicle_no__icontains=q)
        ).values_list("qr_value", flat=True)

        root_qrs = set(resolve_root_qr(x) for x in dispatch_qrs if x)
        rolls_from_dispatch = (
            RollMaster.objects.filter(qr_value__in=list(root_qrs)).values_list("id", flat=True)
            if root_qrs else []
        )

        roll_ids_from_jobs = JobUsage.objects.filter(
            Q(job_id__icontains=q) |
            Q(machine__icontains=q) |
            Q(operator_name__icontains=q)
        ).values_list("roll_id", flat=True)

        ids = set(list(rolls_rm) + list(rolls_from_dispatch) + list(roll_ids_from_jobs))
        rolls = RollMaster.objects.filter(id__in=list(ids)) if ids else RollMaster.objects.none()

    if active_only:
        active_roll_ids = JobUsage.objects.filter(end_time__isnull=True).values_list("roll_id", flat=True)
        rolls = rolls.filter(id__in=list(active_roll_ids))

    rolls = rolls.order_by("-created_at")[:limit]
    results = [build_roll_process_response(r, r.qr_value) for r in rolls]
    return Response({"results": results, "count": len(results)})
