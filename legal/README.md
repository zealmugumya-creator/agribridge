# AgriBridge — Legal & Consent Documents

This folder holds **starting-point templates** for the agreements and consents AgriBridge
needs. They are written specifically for AgriBridge Uganda, but they are **drafts**.

## ⚠️ Read this first

- **I am not a lawyer and this is not legal advice.** These documents are a first draft to
  save you time and money — not finished legal instruments.
- **Have a qualified Ugandan advocate review and finalise every document before you use it.**
  Uganda-specific laws apply, including the **Data Protection and Privacy Act, 2019** and its
  Regulations (2021), the **Electronic Transactions Act**, and consumer-protection and
  contract law. The **investor document especially** touches financial/securities regulation —
  do **not** use it to raise money without a lawyer.
- **Fill in every `[PLACEHOLDER]`** (your registered legal name, address, registration number,
  contact email, effective date, governing law/jurisdiction, etc.).
- Once finalised, register as a **data collector/processor** with Uganda's **Personal Data
  Protection Office (PDPO)** if required for your scale.

## What's here

| File | Purpose | Who accepts it |
|------|---------|----------------|
| `terms-of-use.md` | The main user agreement / rules of the platform | Every user (farmer, buyer, vendor) at sign-up |
| `privacy-policy.md` | How you collect, use, and protect personal data + consent | Every user at sign-up |
| `media-consent.md` | Consent to publish a seller's photos/videos and listing details | Anyone who submits media/listings |
| `investor-nda.md` | Mutual non-disclosure for early investor conversations | You + a prospective investor |

## How to put them live on the app

The web app already has Terms / Privacy / Refund modals. Once these are finalised by your
lawyer, paste the approved text into those modals (in `static/index.html`) and keep a
"Last updated" date. Record each user's acceptance (the sign-up checkbox = their consent).

## Consent you should capture at sign-up

At minimum, the registration form should require the user to tick:
> "I have read and agree to the **Terms of Use** and **Privacy Policy**, and I consent to
> AgriBridge processing my personal data as described."

For sellers submitting photos/videos, also capture the **media consent** (see that file).
