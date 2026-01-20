"""
Run this once to create prerequisite analyzers and classifier for the IDP app.
"""

import json
import os
from typing import Any, Dict

import requests
from azure.identity import DefaultAzureCredential
from dotenv import find_dotenv, load_dotenv

from content_understanding_client import AzureContentUnderstandingClient


API_VERSION = "2025-11-01"
CLASSIFIER_ID = "classifier_idp"
ANALYZER_INVOICES_ID = "analyzer_invoices"
ANALYZER_BANK_STATEMENTS_ID = "analyzer_bank_statements"
ANALYZER_LOAN_ID = "analyzer_loan"


def token_provider() -> str:
    credential = DefaultAzureCredential()
    token = credential.get_token("https://cognitiveservices.azure.com/.default")
    return token.token


def build_client() -> AzureContentUnderstandingClient:
    endpoint = os.getenv("AZURE_AI_ENDPOINT", "").strip()
    api_key = os.getenv("AZURE_AI_API_KEY", "").strip()
    if not endpoint:
        raise ValueError("AZURE_AI_ENDPOINT is missing.")

    return AzureContentUnderstandingClient(
        endpoint=endpoint,
        api_version=API_VERSION,
        subscription_key=api_key if api_key else None,
        token_provider=token_provider if not api_key else None,
        x_ms_useragent="cu-idp-prereq",
    )


def analyzer_exists(client: AzureContentUnderstandingClient, analyzer_id: str) -> bool:
    try:
        client.get_analyzer_detail_by_id(analyzer_id)
        return True
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return False
        raise


def create_analyzer(
    client: AzureContentUnderstandingClient,
    analyzer_id: str,
    analyzer_template: Dict[str, Any],
    skip_existing: bool,
) -> None:
    if analyzer_exists(client, analyzer_id):
        if skip_existing:
            print(f"Analyzer '{analyzer_id}' already exists. Skipping.")
            return
        raise RuntimeError(f"Analyzer '{analyzer_id}' already exists.")

    print(f"Creating analyzer '{analyzer_id}'...")
    response = client.begin_create_analyzer(
        analyzer_id=analyzer_id,
        analyzer_template=analyzer_template,
    )
    client.poll_result(response)
    print(f"Analyzer '{analyzer_id}' created.")


def create_classifier(
    client: AzureContentUnderstandingClient,
    classifier_id: str,
    classifier_template: Dict[str, Any],
    skip_existing: bool,
) -> None:
    if analyzer_exists(client, classifier_id):
        if skip_existing:
            print(f"Classifier '{classifier_id}' already exists. Skipping.")
            return
        raise RuntimeError(f"Classifier '{classifier_id}' already exists.")

    print(f"Creating classifier '{classifier_id}'...")
    response = client.begin_create_analyzer(
        analyzer_id=classifier_id,
        analyzer_template=classifier_template,
    )
    client.poll_result(response)
    print(f"Classifier '{classifier_id}' created.")


def build_invoice_analyzer() -> Dict[str, Any]:
    return {
        "baseAnalyzerId": "prebuilt-document",
        "description": "Invoice analyzer that extracts vendor and line items",
        "config": {
            "returnDetails": True,
            "enableOcr": True,
            "enableLayout": True,
            "estimateFieldSourceAndConfidence": True,
        },
        "fieldSchema": {
            "name": "InvoiceFields",
            "fields": {
                "VendorName": {
                    "type": "string",
                    "method": "extract",
                    "description": "Name of the vendor or supplier, typically in the header.",
                },
                "Items": {
                    "type": "array",
                    "method": "generate",
                    "description": "List of items or services on the invoice.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "Description": {"type": "string", "description": "Item description"},
                            "Amount": {"type": "number", "description": "Line total amount"},
                        },
                    },
                },

                "InvoiceNumber": {
                    "type": "string",
                    "method": "extract",
                    "description": "Invoice identifier (e.g., INV-100).",
                },
                "InvoiceDate": {
                    "type": "string",
                    "method": "extract",
                    "description": "Invoice issue date.",
                },
                "DueDate": {
                    "type": "string",
                    "method": "extract",
                    "description": "Invoice due date.",
                },
                "CustomerName": {
                    "type": "string",
                    "method": "extract",
                    "description": "Customer name (top-right block).",
                },
                "ServicePeriod": {
                    "type": "string",
                    "method": "extract",
                    "description": "Service period range (e.g., 10/14/2019 – 11/14/2019).",
                },
                "CustomerId": {
                    "type": "string",
                    "method": "extract",
                    "description": "Customer identifier (e.g., CID-12345).",
                },
            },
        },
        "models": {"completion": "gpt-4.1-mini"},
        "tags": {"doc_type": "Invoices", "demo": "invoice"},
    }


def build_bank_statement_analyzer() -> Dict[str, Any]:
    return {
        "baseAnalyzerId": "prebuilt-document",
        "description": "Bank statement analyzer that extracts account and balance details",
        "config": {
            "returnDetails": True,
            "enableOcr": True,
            "enableLayout": True,
            "estimateFieldSourceAndConfidence": True,
        },
        "fieldSchema": {
            "name": "BankStatementFields",
            "fields": {
                "BankName": {
                    "type": "string",
                    "method": "generate",
                    "description": "Name of the bank issuing the statement.",
                },
                "AccountHolder": {
                    "type": "string",
                    "method": "generate",
                    "description": "Account holder name.",
                },
                "AccountNumber": {
                    "type": "string",
                    "method": "generate",
                    "description": "Account number shown on the statement.",
                },
                "StatementStartDate": {
                    "type": "date",
                    "method": "generate",
                    "description": "Statement period start date.",
                },
                "StatementEndDate": {
                    "type": "date",
                    "method": "generate",
                    "description": "Statement period end date.",
                },
                "BeginningBalance": {
                    "type": "number",
                    "method": "generate",
                    "description": "Opening balance for the period.",
                },
                "EndingBalance": {
                    "type": "number",
                    "method": "generate",
                    "description": "Closing balance for the period.",
                },
                "TotalDeposits": {
                    "type": "number",
                    "method": "generate",
                    "description": "Sum of deposits in the statement period.",
                },
                "TotalWithdrawals": {
                    "type": "number",
                    "method": "generate",
                    "description": "Sum of withdrawals in the statement period.",
                },
            },
        },
        "models": {"completion": "gpt-4.1-mini"},
        "tags": {"doc_type": "Bank Statements", "demo": "bank-statement"},
    }


def build_loan_analyzer() -> Dict[str, Any]:
    return {
        "baseAnalyzerId": "prebuilt-document",
        "description": "Loan application analyzer - extracts key information",
        "config": {
            "returnDetails": True,
            "enableLayout": True,
            "enableFormula": False,
            "estimateFieldSourceAndConfidence": True,
        },
        "fieldSchema": {
            "fields": {
                "ApplicationDate": {
                    "type": "date",
                    "method": "generate",
                    "description": "Date when the loan application was submitted.",
                },
                "ApplicantName": {
                    "type": "string",
                    "method": "generate",
                    "description": "Full name of the loan applicant or company.",
                },
                "LoanAmountRequested": {
                    "type": "number",
                    "method": "generate",
                    "description": "Total loan amount requested by the applicant.",
                },
                "LoanPurpose": {
                    "type": "string",
                    "method": "generate",
                    "description": "Stated purpose or reason for the loan.",
                },
                "CreditScore": {
                    "type": "number",
                    "method": "generate",
                    "description": "Credit score of the applicant, if available.",
                },
                "Summary": {
                    "type": "string",
                    "method": "generate",
                    "description": "Brief summary overview of the loan application details.",
                },
            }
        },
        "models": {"completion": "gpt-4.1-mini"},
        "tags": {"doc_type": "Loan Application Form", "demo": "loan-application"},
    }


def build_classifier_template() -> Dict[str, Any]:
    return {
        "baseAnalyzerId": "prebuilt-document",
        "description": "Classifier for Invoices, Bank Statements, and Loan Application Forms",
        "config": {
            "returnDetails": True,
            "enableSegment": True,
            "contentCategories": {
                "Invoices": {
                    "description": "Invoices and billing documents.",
                },
                "Bank Statements": {
                    "description": "Bank statements and account activity summaries.",
                },
                "Loan Application Form": {
                    "description": "Loan or application forms and related submissions.",
                },
            },
        },
        "models": {"completion": "gpt-4.1-mini"},
        "tags": {"demo_type": "idp-classifier"},
    }


def main() -> None:
    load_dotenv(find_dotenv())
    client = build_client()

    ids = {
        "classifier_id": CLASSIFIER_ID,
        "analyzer_a_id": ANALYZER_INVOICES_ID,
        "analyzer_b_id": ANALYZER_BANK_STATEMENTS_ID,
        "analyzer_c_id": ANALYZER_LOAN_ID,
    }

    create_analyzer(client, ids["analyzer_a_id"], build_invoice_analyzer(), skip_existing=True)
    create_analyzer(client, ids["analyzer_b_id"], build_bank_statement_analyzer(), skip_existing=True)
    create_analyzer(client, ids["analyzer_c_id"], build_loan_analyzer(), skip_existing=True)
    create_classifier(client, ids["classifier_id"], build_classifier_template(), skip_existing=True)

    print("\nCreated resources:")
    print(json.dumps(ids, indent=2))
    print("\nSuggested environment variables:")
    print(f"CLASSIFIER_ID={ids['classifier_id']}")
    print(f"ANALYZER_ID_A={ids['analyzer_a_id']}")
    print(f"ANALYZER_ID_B={ids['analyzer_b_id']}")
    print(f"ANALYZER_ID_C={ids['analyzer_c_id']}")

if __name__ == "__main__":
    main()
