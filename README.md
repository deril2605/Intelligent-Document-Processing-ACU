# Intelligent Document Review App (app_main.py) and Setup Script (prereq.py)

This folder contains the Streamlit review app (`app_main.py`) and a one-time setup script
(`prereq.py`) that creates the required Azure Content Understanding resources.

The app implements the PRD workflow:
1) Upload a PDF.
2) Classify it into one of three document types.
3) Route to the matching analyzer.
4) Extract fields.
5) Show fields and highlight their bounding boxes for human validation.

--------------------------------------------------------------------------------

## Contents

- app_main.py
  Streamlit UI for classification, extraction, and visual validation.
- prereq.py
  One-time setup script to create the classifier and three analyzers.

--------------------------------------------------------------------------------

## Prerequisites

1) Python 3.8+.
2) Azure Content Understanding resource.
3) Install required packages (typical):

   pip install streamlit python-dotenv azure-identity pymupdf pillow requests

4) .env configured at repo root:
   - AZURE_AI_ENDPOINT
   - AZURE_AI_API_KEY (optional if using DefaultAzureCredential)
   - CU_PRICE_PER_1K_INPUT (Standard pricing)
   - CU_PRICE_PER_1K_OUTPUT (Standard pricing)

   Example:
   AZURE_AI_ENDPOINT=https://your-resource.services.ai.azure.com/
   AZURE_AI_API_KEY=...
   CU_PRICE_PER_1K_INPUT=0.00275
   CU_PRICE_PER_1K_OUTPUT=0.011

--------------------------------------------------------------------------------

## One-time Setup: prereq.py

### What it does

`prereq.py` creates the resources required by the app:

- Classifier: classifier_idp
- Analyzer: analyzer_invoices
- Analyzer: analyzer_bank_statements
- Analyzer: analyzer_loan

Each analyzer uses the custom field schema defined in the script.
The classifier routes documents into:
- Invoices
- Bank Statements
- Loan Application Form

The script uses `begin_create_analyzer()` and `poll_result()` from
`content_understanding_client.py`.

### Run it

python idp_app/prereq.py

It will skip creation if the IDs already exist.

### If you need different fields

Edit the field schemas inside:
- build_invoice_analyzer()
- build_bank_statement_analyzer()
- build_loan_analyzer()

Each schema supports primitives, arrays, and objects.

--------------------------------------------------------------------------------

## App: app_main.py

### What it does

`app_main.py` is a Streamlit app that:

1) Loads a PDF.
2) Classifies it using the classifier analyzer.
3) Picks the correct analyzer by label.
4) Extracts fields and source regions.
5) Renders the PDF and highlights selected fields.

### Modes

1) Live (Azure)
   - Uses the Azure service to classify and extract.
   - Requires valid AZURE_AI_ENDPOINT and auth.
   - Uses the fixed IDs:
     - classifier_idp
     - analyzer_invoices
     - analyzer_bank_statements
     - analyzer_loan

2) Offline (saved JSON)
   - Loads a local JSON response and PDF.
   - No Azure calls.
   - Useful for demos.

### How classification works

The classifier is created as an analyzer. Classification calls:

client.begin_analyze_binary(analyzer_id=classifier_idp, file_location=pdf)

The result is scanned for the first category label and optional confidence.
That label is mapped to the analyzer ID:

- "Invoices" -> analyzer_invoices
- "Bank Statements" -> analyzer_bank_statements
- "Loan Application Form" -> analyzer_loan

### Field extraction

After routing, the app calls:

client.begin_analyze_binary(analyzer_id=<mapped analyzer>, file_location=pdf)

The response is parsed to extract:
- field name
- value
- source regions (bounding boxes)

The parser is defensive and handles multiple output shapes, including:
- fields map under result.contents[*].fields
- nested sources or source strings (D(...) format)
- boundingRegions or polygon arrays

### Bounding box overlay

Bounding boxes are drawn on the rendered PDF pages.
If page dimensions exist in the result, they are used to scale coordinates.
If not, raw coordinates are drawn (may require adjustments if units differ).

### Session cache

To avoid repeated calls and slow re-renders, the app caches:
- analysis results
- extracted fields
- rendered page images

It re-runs classification/extraction only when the file changes.
It re-renders the PDF only when the zoom changes.

### Usage and cost estimation

After extraction, the app displays:
- model(s)
- input/output/total tokens
- estimated cost (if pricing env vars are set)

Cost is estimated using:
- CU_PRICE_PER_1K_INPUT
- CU_PRICE_PER_1K_OUTPUT

This is only the model token estimate. It does not include
page-based extraction or contextualization meters.

--------------------------------------------------------------------------------

## Running the App

From repo root:

streamlit run idp_app/app_main.py

Then:
1) Choose "Live (Azure)".
2) Upload a PDF.
3) Click Run.
4) Select fields on the left to see their sources highlighted.

--------------------------------------------------------------------------------

## Environment Variables Used

Required:
- AZURE_AI_ENDPOINT

Optional (auth):
- AZURE_AI_API_KEY

Optional (cost estimate):
- CU_PRICE_PER_1K_INPUT
- CU_PRICE_PER_1K_OUTPUT

Hardcoded IDs:
- classifier_idp
- analyzer_invoices
- analyzer_bank_statements
- analyzer_loan

--------------------------------------------------------------------------------

## Troubleshooting

### 404 Resource Not Found

Cause:
- The classifier/analyzers were not created, or
- AZURE_AI_ENDPOINT points to a different resource.

Fix:
- Run prereq.py.
- Confirm AZURE_AI_ENDPOINT matches the resource where you created them.

### No fields found

Cause:
- Response shape mismatch or analyzer returned no fields.

Fix:
- Inspect analyzer schema.
- Check the response in the expander (add one if needed).

### Boxes do not align

Cause:
- Page dimensions missing or unit mismatch.

Fix:
- Ensure the analyzer returns page width/height.
- Adjust scaling in draw_regions_on_page() if needed.

--------------------------------------------------------------------------------

## Customize Field Schemas

The field schemas live in prereq.py.
To update, edit:
- build_invoice_analyzer()
- build_bank_statement_analyzer()
- build_loan_analyzer()

### Current Field Structures

#### Invoices (analyzer_invoices)

- VendorName (string, extract)
- Items (array, generate)
  - Description (string)
  - Amount (number)

#### Bank Statements (analyzer_bank_statements)

- BankName (string, generate)
- AccountHolder (string, generate)
- AccountNumber (string, generate)
- StatementStartDate (date, generate)
- StatementEndDate (date, generate)
- BeginningBalance (number, generate)
- EndingBalance (number, generate)
- TotalDeposits (number, generate)
- TotalWithdrawals (number, generate)

#### Loan Application Form (analyzer_loan)

- ApplicationDate (date, generate)
- ApplicantName (string, generate)
- LoanAmountRequested (number, generate)
- LoanPurpose (string, generate)
- CreditScore (number, generate)
- Summary (string, generate)

After editing, re-run prereq.py to recreate analyzers (delete old ones first
if you do not want to reuse the same IDs).

--------------------------------------------------------------------------------

## Notes

- The classifier is created as an analyzer and called via analyzeBinary.
- The app avoids showing raw JSON to keep the UI business-friendly.
- The app uses DefaultAzureCredential if AZURE_AI_API_KEY is empty.
