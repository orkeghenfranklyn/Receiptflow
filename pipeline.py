import os, json, time
from datetime import date, datetime
from google import genai
from google.genai import types
from PIL import Image

# Model names retire often. Update this list if you see 404 errors.
MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]

CONFIDENCE_THRESHOLD = 0.80
MIN_SIDE = 400
MAX_AGE_DAYS = 365
REQUIRED = ["vendor", "date", "amount", "currency", "category"]

PROMPT = """You read receipts. Return ONLY JSON with these keys:
vendor (string), date (YYYY-MM-DD; receipt dates are usually DD/MM/YYYY),
amount (number: the total paid), currency (3-letter code like NGN or USD),
category (one of: Supplies, Transport, Materials, Marketing, Software, Equipment, Other),
confidence (0 to 1: how sure you are that everything is correct).
If something is unreadable, use null. Never guess."""

EMPTY = {"vendor": "", "date": "", "amount": None,
         "currency": "", "category": "", "confidence": 0.0}


def get_client(api_key=None):
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return genai.Client(api_key=key)


def ai_extract(image, client, tries_per_model=3):
    for model in MODELS:
        for attempt in range(tries_per_model):
            try:
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
                busy = "503" in msg or "429" in msg
                if busy and attempt < tries_per_model - 1:
                    time.sleep(2 ** (attempt + 1))
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
