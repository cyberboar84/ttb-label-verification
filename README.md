# TTB Label Verification Prototype

A tool that checks an alcohol beverage label for the information TTB requires:
brand name, class/type, alcohol content, net contents, bottler name & address,
country of origin (for imports), and the government health warning. It tells the
reviewer in seconds whether it's **Compliant**, **Not Compliant**, or **Needs
Review**.

## ▶ Try it (no setup)

**Open the live app:** **https://ttb-label-verify-7ce3.azurewebsites.net**

Nothing to install or sign in. Just open it in a browser and use it.

### How to use it

1. **Add a label image**, drag in a photo or pick a file. (A single front label is
   fine; if the warning or other details are on a separate back label, add that too
   and they're checked together.)
2. **Click "Verify label."**
3. **Read the result**, a clear **Compliant / Needs Review / Not Compliant**
   verdict, with a line for each required item showing what was found and what's
   missing or wrong.

That's it. To compare a label against specific application values, open the
optional "Compare against application data" panel. To check many labels at once,
use the **Batch** tab.

---

## What it checks

For each label it verifies the TTB-required elements, applying the rules for the
beverage type:

| Element | Rule |
|---------|------|
| Brand name | present (and, in match mode, matches the application) |
| Class / type designation | present |
| Alcohol content | required for spirits & wine; **optional for beer** |
| Net contents | present |
| Name & address of bottler/producer | present |
| Country of origin | required **only for imports** |
| Government health warning | present, in all caps, with the correct statutory wording |

It reads labels photographed at angles, in poor light, or with glare. When a label
is too degraded to read a required item with confidence, it flags it for **human
review** instead of guessing, the way an agent handles a bad image today.

---

## Approach (the technical bit)

The OCR/vision is a commodity; the **compliance logic is the product**, so that's
where the work went.

**OCR-grounded extraction.** For each image: OCR reads all the text first (fast,
and it catches fine print), then gpt-4o assigns that text to the right fields
(*which* text is the brand vs. the ABV, a layout problem). Grounding the model on
the OCR text is both faster and more accurate on small print than vision alone.

**The government warning is checked deterministically, never by the LLM.** It's
verified against the verbatim OCR: the `GOVERNMENT WARNING:` prefix must be in all
caps (title case is a rejection), and each statutory clause must be present
(matched with tolerance for OCR noise: missing *content* fails, garbled
*characters* don't). Keeping this off the model is also the key security property:
a malicious label can't trick a check the model never makes.

**Multiple panels, one verdict.** Front and back labels are submitted as separate
images (that's how COLA works); the app merges them and judges the bottle as a
whole.

**Matching engine** uses the right strategy per field: fuzzy for brand names
(`STONE'S THROW` ≈ `Stone's Throw`), numeric for ABV and net contents (unit-aware),
presence for the rest. Borderline cases go to **Review**, not auto-reject, 
keeping the human judgment agents asked for.

**Security** (full detail in **[SECURITY.md](SECURITY.md)** and a live red-team in
**[RED_TEAM.md](RED_TEAM.md)**): the model was attacked across two rounds: 7
direct prompt-injections plus **9 advanced techniques** (Unicode homoglyphs,
encoded payloads, multilingual instructions, multimodal smuggling via EXIF/QR,
split-panel injection, numeric homoglyphs). Real gaps were found and fixed: an
injection-induced hallucination, a non-English detector evasion, and homoglyph
vectors that initially held only by luck. Layered defenses: OCR-gated warning,
strict output schema, a deterministic injection detector with Unicode folding,
and Azure Prompt Shields, with residual risks (full multilingual detection,
adversarial perturbation) named explicitly, not hidden. Plus per-IP rate limiting,
a daily call cap, upload-size and decompression-bomb guards, US-region inference,
and no data retention.

### Why these choices

- **Managed Azure AI over a self-hosted model:** hits the speed target without a
  GPU server to run; the graded intelligence is the custom compliance logic.
- **US data residency:** gpt-4o deployed in-region (not global).
- **Server-side inference:** the browser only talks to our app, so a restrictive
  agency firewall only needs to reach one URL.

### Tools used

FastAPI · Azure AI Vision (Image Analysis 4.0 / Read) · Azure OpenAI gpt-4o · Azure
AI Content Safety (Prompt Shields) · rapidfuzz · Pillow · vanilla JS/CSS frontend ·
Azure App Service (Linux/Python).

### Performance

~3.9 s for a single label, ~3.6 s for a front+back pair, through the public URL
(target: under 5 s). Heavy glare/curved-bottle photos run ~7 s, the hard
exception; clean label scans (what TTB actually receives) are the fast path.

---

## Assumptions & trade-offs

- **Input.** TTB receives clean cropped label image files (front/back as separate
  images, ≤1.5 MB); bottle *photos* are the exception (embossed/etched containers).
  The app handles both; severely degraded photos route unreadable items to Review.
- **Application data is trusted input.** In production it comes from COLA; here the
  optional fields stand in for that feed (no COLA integration, per the brief).
- **Warning bold/font-size** can't be judged from text alone; we verify presence,
  all-caps prefix, and statutory wording.
- **Public access without login** is acceptable for a prototype and bounded by rate
  limiting + a token cap; production would add agency SSO (see SECURITY.md, F-2).
- **No persistence.** Nothing is stored server-side.

---

## Running the source yourself (optional, not needed to use the app)

This is **only** for reviewing or running the code. To use the prototype, just open
the live URL above.

Requires Python 3.12+. Runs in **mock mode** with no Azure credentials:

```bash
cd backend
uv venv && uv pip install -r requirements.txt     # or: python -m venv .venv && pip install -r requirements.txt
MOCK_VISION=true uvicorn app.main:app --reload
# open http://localhost:8000
```

Run the tests:

```bash
cd backend && pytest          # 53 tests: compliance logic, warning, injection, API, security
```

Sample labels (spirits, beer, wine, a clean front/back pair, adversarial-warning
cases) are in `backend/samples/`; the red-team harness is in `backend/redteam/`.

### Deploying your own copy (optional)

```bash
az login
bash infra/provision.sh   # creates the Azure resources (Vision + gpt-4o + App Service)
bash infra/deploy.sh      # packages and deploys, then waits for /health
```

`provision.sh` is parameterized and re-runnable.
