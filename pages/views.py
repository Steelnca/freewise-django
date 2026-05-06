
from django.shortcuts import render
from django.views.generic import TemplateView
from django.contrib.messages import success

# Create your views here.

class HomeView(TemplateView):
    template_name = "pages/home.html"


class AboutView(TemplateView):
    template_name = "pages/about.html"