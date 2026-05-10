"""
services/gemini_client.py — COMPATIBILITY SHIM
Redirects all imports to services.grok_client.
This file exists only for backward compatibility.
"""
from services.grok_client import grok, grok as gemini, GrokClient, GrokResponse, DEMO_OUTPUTS
