from django.db import models

class RollMaster(models.Model):
    # Business roll id (operator entered)
    roll_id = models.CharField(max_length=50, unique=True)

    # ✅ SCANNED QR VALUE (can be Paytm/UPI/any QR string)
    qr_value = models.TextField(unique=True, null=True, blank=True)

    supplier = models.CharField(max_length=100, null=True, blank=True)
    gsm = models.IntegerField(null=True, blank=True)
    width = models.IntegerField(null=True, blank=True)
    lot_no = models.CharField(max_length=30, null=True, blank=True)

    status = models.CharField(max_length=30, default="IN_STOCK")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.roll_id


class JobUsage(models.Model):
    roll = models.ForeignKey(RollMaster, on_delete=models.CASCADE)

    # Your flexo start/end data (keep simple for now)
    job_id = models.CharField(max_length=50, null=True, blank=True)
    machine = models.CharField(max_length=50, null=True, blank=True)

    operator_name = models.CharField(max_length=80, null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)

    meters_printed = models.FloatField(null=True, blank=True)
    wastage = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"{self.roll.roll_id} - {self.job_id}"


class Dispatch(models.Model):
    qr_value = models.TextField(null=True, blank=True)  # current phase QR
    operator_name = models.CharField(max_length=80, null=True, blank=True)
    vehicle_no = models.CharField(max_length=50, null=True, blank=True)
    dispatch_qty = models.FloatField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    customer = models.CharField(max_length=100, null=True, blank=True)
    invoice_no = models.CharField(max_length=50, null=True, blank=True)

    dispatched_at = models.DateTimeField(auto_now_add=True)


class DeliveryConfirmation(models.Model):
    qr_value = models.TextField(null=True, blank=True)  # current phase QR
    operator_name = models.CharField(max_length=80, null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    gps_lat = models.FloatField(null=True, blank=True)
    gps_lng = models.FloatField(null=True, blank=True)
    confirmed_at = models.DateTimeField(auto_now_add=True)


# ✅ THIS IS THE CRITICAL LINKING TABLE
class QRLink(models.Model):
    previous_qr_value = models.TextField()
    current_qr_value = models.TextField()

    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["previous_qr_value"]),
            models.Index(fields=["current_qr_value"]),
        ]

    def __str__(self):
        return f"{self.previous_qr_value} -> {self.current_qr_value}"
