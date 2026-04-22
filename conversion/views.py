import os
import cv2
import time
import json
import threading

from django.http import JsonResponse, HttpResponse
from urllib.parse import quote
from django.shortcuts import render, redirect
from django.conf import settings
from django.views.decorators.http import require_http_methods

from conversion.core.core import generate_eft, section_fp, manual_section
from conversion.core.pdf_loader import pdf_to_image_bytes

CWD = os.getcwd()
TMP_DIR = settings.TMP_DIR
FILES = []  # List to hold generated files

# Create your views here.

def index(request):
    return render(request, "conversion/index.html")

def new(request):
    return render(request, "conversion/new.html")

def process_fp():
    os.chdir(TMP_DIR)
    for each in RESULTS:
        each.convert()

def resection(request):
    if request.method == "POST":
        data = request.POST.dict()
        fname = os.path.join(TMP_DIR, 'input.png')
        out = manual_section(fname=fname, data=data)
        return JsonResponse({'values': out}, safe=False)
    return JsonResponse({'message': 'Invalid request method'}, status=405)

def step1(request):
    global RESULTS
    if request.method == "POST":
        file = request.FILES.get("formFileLg")
        print(file)
        time.sleep(1)
        warning = None
        original_name = (file.name or "").lower()
        if original_name.endswith(".pdf"):
            try:
                img_bytes, ext, warning = pdf_to_image_bytes(file.read())
            except ValueError as e:
                print(f"PDF load failed: {e}")
                return JsonResponse({"values": False, "warning": str(e)}, safe=False)
            intermediate = os.path.join(TMP_DIR, f"input.{ext}")
            with open(intermediate, "wb") as dest:
                dest.write(img_bytes)
            fname = os.path.join(TMP_DIR, "input.png")
            if ext != "png":
                img = cv2.imread(intermediate)
                cv2.imwrite(fname, img)
            else:
                # Already a PNG; the intermediate IS input.png
                if intermediate != fname:
                    os.replace(intermediate, fname)
        else:
            fname = os.path.join(TMP_DIR, "input.png")
            with open(fname, "wb+") as dest:
                for chunk in file.chunks():
                    dest.write(chunk)
        try:
            out = section_fp(fname=fname)
        except Exception as e:
            print(e)
            out = False
        return JsonResponse({"values": out, "warning": warning}, safe=False)
    return JsonResponse({"message": "Invalid request method"}, status=405)

def step2(request):
    global FILES
    if request.method == "POST":
        print(dir(request))
        print(request.body)
        data = request.POST.dict()
        print(data)
        fname = generate_eft(data)
        FILES.append(fname)

    # Get the file sizes and add them to the context
    file_info = []
    for file in FILES:
        file_path = os.path.join(TMP_DIR, file)
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            # Convert the size to MB and round it to 1 decimal place
            file_size_mb = round(file_size / (1024 * 1024), 1)
            file_info.append({'name': file, 'size': file_size_mb})
    
    return render(request, "conversion/download.html", context={'files': file_info})

def download(request, filename):
    """
    Handle the download of files.
    """
    file_path = os.path.join(TMP_DIR, filename)
    
    # Check if the file exists
    if os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/octet-stream")
            response['Content-Disposition'] = f'attachment; filename={quote(filename)}'
            return response
    else:
        return JsonResponse({"message": "File not found"}, status=404)

@require_http_methods(["DELETE"])
def delete_file(request, filename):
    """
    Handles the deletion of files from the server
    """
    global FILES
    file_path = os.path.join(TMP_DIR, filename)

    # Check if the file exists and remove it
    if os.path.exists(file_path):
        os.remove(file_path)
        FILES = [f for f in FILES if f != filename]  # Remove the file from the FILES list
        return JsonResponse({"message": "File deleted successfully"}, status=200)
    else:
        return JsonResponse({"message": "File not found"}, status=404)
