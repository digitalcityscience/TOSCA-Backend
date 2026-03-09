from django.shortcuts import render
from django.contrib.auth.decorators import login_required
# Create your views here.

@login_required
def console(request):
    return render(request, 'geo_console/geodata_console.html')
