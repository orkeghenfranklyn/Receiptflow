import streamlit as st
import pandas as pd
from PIL import Image, ImageOps
import pipeline
from pipeline import get_client, process_receipt

st.set_page_config(page_title="ReceiptFlow", page_icon="🧾")
st.title("🧾 ReceiptFlow")
st.caption("Demo only. Use sample receipts, not private ones. "
           "Uploaded images are sent to Google's AI to be read.")

# Optional password gate, so strangers can't use up the free AI quota
password = st.secrets.get("APP_PASSWORD")
if password:
    if st.text_input("Password", type="password") != password:
        st.stop()

if "rows" not in st.session_state:
    st.session_state.rows = []
    st.session_state.seen = set()
    st.session_state.done = set()

files = st.file_uploader("Upload receipt photos", type=["png", "jpg", "jpeg"],
                         accept_multiple_files=True)

if st.button("Process receipts") and files:
    client = get_client(st.secrets["GEMINI_API_KEY"])
    todo = [f for f in files if (f.name, f.size) not in st.session_state.done][:5]
    if len(files) > len(todo):
        st.info("Max 5 new receipts per run (protects the free AI quota).")
    for f in todo:
        with st.spinner(f"Reading {f.name}..."):
            img = ImageOps.exif_transpose(Image.open(f)).convert("RGB")
            r = process_receipt(img, client, st.session_state.seen)
            r["file"] = f.name
            st.session_state.rows.append(r)
            st.session_state.done.add((f.name, f.size))

if st.session_state.rows:
    cols = ["file", "vendor", "date", "amount", "currency",
            "category", "confidence", "status", "notes"]
    df = pd.DataFrame(st.session_state.rows)[cols]
    st.dataframe(df, use_container_width=True)
    n = (df["status"] == "Needs Review").sum()
    st.write(f"**{len(df) - n} OK, {n} need review.** Always check flagged rows by hand.")
    st.download_button("Download CSV", df.to_csv(index=False),
                       "receipts.csv", "text/csv")
   if pipeline.LAST_ERRORS:
      with st.expander("Technical errors (for debugging)"):
        st.code("\n".join(pipeline.LAST_ERRORS[-6:]))
