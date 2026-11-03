import os, json, time
from datetime import date, datetime
from google import genai
from google.genai import types
from PIL import Image

# Model names retire often. Update this list if you see 404 errors.
# Free-tier limits are per model, so more models = more capacity.
MODELS = ["gemini-3.8-flash", "gemini-3.6-flash",
          "gemini-3.5-flash", "gemini-3.1-flash-lite"]

CONFIDENCE_THRESHOLD = 0.80
MIN_SIDE = 400
MAX_AGE_DAYS = 365
MIN_GAP = 13  # seconds between AI calls (free tier: 5 per minute per model)
REQUIRED = ["vendor", "date", "amount", "currency", "category"]

PROMPT = """You read receipts. Return ONLY JSON with these keys:
vendor (string), date (YYYY-MM-DD; receipt dates are usually DD/MM/YYYY),
amount (number: the total paid), currency (3-letter code like NGN or USD),
category (one of: Supplies, Transport, Materials, Marketing, Software, Equipment, Other),
confidence (0 to 1: how sure you are that everything is correct).
If something is unreadable, use null. Never guess."""

EMPTY = {"vendor": "", "date": "", "amount": None,
         "currency": "", "category": "", "confidence": 0.0}

# Errors are collected here so the app can show them for debugging
LAST_ERRORS = []
_last_call = [0.0]


def _pace():
    wait = MIN_GAP - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def get_client(api_key=None):
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return genai.Client(api_key=str(key).strip())


def ai_extract(image, client, tries_per_model=3):
    for model in MODELS:
        for attempt in range(tries_per_model):
            try:
                _pace()
                resp = client.models.generate_content(
                    model=model,
                    contents=[image, PROMPT],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"),
                )
                data = json.loads(resp.text)
                out = {k: data.get(k, EMPTY[k]) for k in EMPTY}
                if not isinstance(out["confidence"], (int, float)):
                    out["confidence"] = 0.0
                return out
            except Exception as e:
                msg = str(e)
                LAST_ERRORS.append(f"{model} (try {attempt + 1}): {msg[:300]}")
                if "429" in msg:
                    break  # quota hit: go to the next model right away
                if "503" in msg and attempt < tries_per_model - 1:
                    time.sleep(2 ** (attempt + 1))  # busy server: wait, retry
                    continue
                break
    return dict(EMPTY)  # all failed: becomes "Needs Review", never a fake OK


def validate(r, image=None):
    problems = [f"missing {f}" for f in REQUIRED if r[f] in ("", None)]
    if r["amount"] not in ("", None):
        if not isinstance(r["amount"], (int, float)) or r["amount"] <= 0:
            problems.append("invalid amount")
    if r["date"] not in ("", None):
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
            age = (date.today() - d).days
            if age < 0:
                problems.append("date in future")
            elif age > MAX_AGE_DAYS:
                problems.append("date older than 1 year")
        except ValueError:
            problems.append("invalid date")
    if image is not None and max(image.size) < MIN_SIDE:
        problems.append("image too small")
    if r["confidence"] < CONFIDENCE_THRESHOLD:
        problems.append("low confidence")
    return problems


def is_duplicate(r, seen):
    # Empty or unreadable receipts can't be compared, so never call them duplicates
    if not r["vendor"] or not r["date"] or r["amount"] is None:
        return False
    key = (str(r["vendor"]).lower(), r["date"], r["amount"])
    if key in seen:
        return True
    seen.add(key)
    return False


def process_receipt(image, client, seen):
    r = ai_extract(image, client)
    problems = validate(r, image)
    if is_duplicate(r, seen):
        problems.append("duplicate")
    r["status"] = "Needs Review" if problems else "OK"
    r["notes"] = "; ".join(problems)
    return r
