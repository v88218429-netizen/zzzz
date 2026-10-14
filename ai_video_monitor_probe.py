#!/usr/bin/env python3
"""Non-destructive connectivity diagnostic for AI Video Monitor."""
import json
import os
import urllib.error
import urllib.request

def google_probe():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    raw = os.environ.get("CLASPRC_JSON", "")
    if not raw:
        print("GOOGLE_AUTH=missing")
        return
    try:
        cfg = json.loads(raw)["tokens"]["default"]
        cred = Credentials(token=cfg.get("access_token"), refresh_token=cfg.get("refresh_token"),
                           token_uri="https://oauth2.googleapis.com/token",
                           client_id=cfg.get("client_id"), client_secret=cfg.get("client_secret"))
        cred.refresh(Request())
        url = "https://sheets.googleapis.com/v4/spreadsheets/1ejXreSQYBDDlTDI9ZtRs7hW0vgdssVRm__hNtxJmP64/values/%27%D0%97%D0%B0%D0%B4%D0%B0%D0%BD%D0%B8%D1%8F%27%21A1%3AC2"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + cred.token})
        with urllib.request.urlopen(req, timeout=25) as res:
            body = json.load(res)
        print("GOOGLE_SHEETS_READ=OK", "rows=" + str(len(body.get("values", []))))
    except urllib.error.HTTPError as ex:
        body = json.loads(ex.read().decode()).get("error", {})
        msg = body.get("message", "")
        print("GOOGLE_SHEETS_READ=HTTP_"+str(ex.code), "reason="+str(body.get("status", "unknown")), "message="+str(msg)[:170])
    except Exception as ex:
        print("GOOGLE_SHEETS_READ=ERROR_"+type(ex).__name__)

def models_probe():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("AI_MODELS=missing")
        return
    payload = json.dumps({"model": "openai/gpt-4.1-mini", "messages": [{"role": "user", "content": "Answer with exactly OK"}], "max_tokens": 12}).encode()
    req = urllib.request.Request("https://models.github.ai/inference/chat/completions",
        data=payload, method="POST",
        headers={"Authorization": "Bearer "+token, "Content-Type": "application/json", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
            print("AI_HTTP=",res.status,"CT=",res.headers.get("Content-Type"),"BYTES=",len(raw),"PREVIEW=",repr(raw[:90]))
            answer = json.loads(raw)
        print("AI_MODELS=OK", "choices=" + str(len(answer.get("choices", []))))
    except urllib.error.HTTPError as ex:
        print("AI_MODELS=HTTP_"+str(ex.code), "RESPONSE="+ex.read().decode("utf-8","replace")[:120])
    except Exception as ex:
        print("AI_MODELS=ERROR_"+type(ex).__name__)

if __name__ == "__main__":
    google_probe()
    models_probe()
