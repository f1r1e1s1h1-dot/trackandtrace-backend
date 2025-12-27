from rest_framework import serializers
from .models import RollMaster, JobUsage, ChildRoll, Dispatch, DeliveryConfirmation

class RollSerializer(serializers.ModelSerializer):
    class Meta:
        model = RollMaster
        fields = '__all__'

class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobUsage
        fields = '__all__'

class ChildRollSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChildRoll
        fields = '__all__'

class DispatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dispatch
        fields = '__all__'

class DeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryConfirmation
        fields = '__all__'
