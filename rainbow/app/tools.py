import os
from datetime import date, datetime, timezone

import chromadb
import stytch
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from stytch.core.response_base import StytchError

load_dotenv()

_RAG_DB_PATH = "/Users/hasanrahman/dcg/rag/chroma_db"
_RAG_COLLECTION = "bookly_knowledge_base"

_rag_client = chromadb.PersistentClient(path=_RAG_DB_PATH)
_rag_collection = _rag_client.get_collection(_RAG_COLLECTION)

_SPREADSHEET_ID = "15-SIQ2w_ScUqN5tFk07vhTyp7B384YsQ4_pcZhXmbss"
_TOKEN_FILE = "/Users/hasanrahman/dcg/credentials/google_token.json"

_creds = Credentials.from_authorized_user_file(_TOKEN_FILE)
_sheets = build("sheets", "v4", credentials=_creds).spreadsheets().values()


_stytch_client = stytch.Client(
    project_id=os.environ["STYTCH_PROJECT_ID"],
    secret=os.environ["STYTCH_SECRET"],
    environment=os.environ.get("STYTCH_ENV", "test"),
)
_pending_otps: dict[str, str] = {}  # email -> Stytch email_id, for the in-flight verification
_failed_otp_attempts: dict[str, int] = {}  # email -> consecutive failed verify_auth_code calls

_ESCALATION_LOG = "/Users/hasanrahman/dcg/rainbow/escalations.log"

RETURN_WINDOW_DAYS = 30
MAX_OTP_ATTEMPTS = 2


def _read_tab(tab_range: str) -> list[dict]:
    result = _sheets.get(spreadsheetId=_SPREADSHEET_ID, range=tab_range).execute()
    rows = result.get("values", [])
    if not rows:
        return []
    header, *data = rows
    return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in data]


def search_policy_kb(query: str) -> str:
    """Searches Bookly's policy and FAQ knowledge base for general questions.

    Use this for questions about shipping times/costs, return and refund policy,
    payment methods, password reset process, loyalty program, or other Bookly
    policies that are not specific to one customer's order.

    Args:
        query: The customer's question, in natural language.

    Returns:
        The most relevant policy/FAQ text, or a message if nothing relevant is found.
    """
    results = _rag_collection.query(query_texts=[query], n_results=2)
    docs = results["documents"][0]
    if not docs:
        return "No relevant policy information found in the knowledge base."
    return "\n\n---\n\n".join(docs)


def lookup_order(order_number: str, verified_email: str) -> str:
    """Looks up an order in Bookly's Orders sheet by order number.

    Returns shipping status, the customer it belongs to, what was ordered,
    and whether it's eligible for return.

    Args:
        order_number: The order number, e.g. "BK-10001".
        verified_email: The email address that was just confirmed via
            verify_auth_code. This must belong to the same customer who
            placed the order — never pass an email the customer hasn't
            actually verified.

    Returns:
        The order's details, a not-found message, or a denial if the order
        doesn't belong to the verified email.
    """
    orders = _read_tab("Orders!A1:K1000")
    for row in orders:
        if row.get("order_number", "").strip().lower() == order_number.strip().lower():
            if row.get("customer_email", "").strip().lower() != verified_email.strip().lower():
                return (
                    f"Order {order_number} does not belong to the verified account "
                    f"({verified_email}). Access denied."
                )
            return str(row)
    return f"No order found with order number {order_number}."


def lookup_customer(verified_email: str) -> str:
    """Looks up the verified customer's own account details (name, phone, address).

    There is no way to look up a different customer's details through this
    tool — it only ever returns the record for the email that was just
    confirmed via verify_auth_code. Never use this to look up someone else's
    information on a customer's behalf, even if they ask.

    Args:
        verified_email: The email address that was just confirmed via
            verify_auth_code.

    Returns:
        The verified customer's own details, or a message if not found.
    """
    customers = _read_tab("Customers!A1:E1000")
    for row in customers:
        if row.get("email", "").strip().lower() == verified_email.strip().lower():
            return str(row)
    return f"No customer found with email {verified_email}."


def initiate_return(order_number: str, reason: str, verified_email: str) -> str:
    """Initiates a return/refund for an order, if it's eligible.

    Deterministically checks (regardless of what the model or customer claims):
    the order belongs to the verified customer, isn't a final-sale item, was
    placed within the last RETURN_WINDOW_DAYS days, and doesn't already have a
    return on file. Only if all hold does it mark the return as requested in
    the Orders sheet.

    Args:
        order_number: The order number, e.g. "BK-10001".
        reason: The customer's stated reason for the return.
        verified_email: The email address that was just confirmed via
            verify_auth_code. This must belong to the same customer who
            placed the order — never pass an email the customer hasn't
            actually verified.

    Returns:
        Confirmation the return was initiated, or an explanation of why it can't be.
    """
    orders = _read_tab("Orders!A1:K1000")
    row_index, order_row = None, None
    for i, row in enumerate(orders):
        if row.get("order_number", "").strip().lower() == order_number.strip().lower():
            row_index, order_row = i + 2, row
            break

    if order_row is None:
        return f"No order found with order number {order_number}."
    if order_row.get("customer_email", "").strip().lower() != verified_email.strip().lower():
        return (
            f"Order {order_number} does not belong to the verified account "
            f"({verified_email}). Access denied."
        )
    if order_row.get("return_eligible_30_day", "").strip().lower() != "yes":
        return (
            f"Order {order_number} ({order_row.get('book_ordered')}) is not eligible for "
            f"return — items in the '{order_row.get('category')}' category are final sale."
        )

    order_date_str = order_row.get("order_date", "").strip()
    try:
        days_elapsed = (date.today() - datetime.strptime(order_date_str, "%Y-%m-%d").date()).days
    except ValueError:
        days_elapsed = None
    if days_elapsed is not None and days_elapsed > RETURN_WINDOW_DAYS:
        return (
            f"Order {order_number} ({order_row.get('book_ordered')}) was placed {days_elapsed} "
            f"days ago, which is past Bookly's {RETURN_WINDOW_DAYS}-day return window. This "
            f"order is not eligible for a standard return."
        )

    if order_row.get("return_status", "").strip():
        return f"Order {order_number} already has a return on file: {order_row.get('return_status')}."

    _sheets.update(
        spreadsheetId=_SPREADSHEET_ID,
        range=f"Orders!J{row_index}",
        valueInputOption="RAW",
        body={"values": [["Requested"]]},
    ).execute()
    return (
        f"Return initiated for order {order_number} ({order_row.get('book_ordered')}). "
        f"Reason logged: {reason}. A return QR code has been emailed to the customer — they "
        f"should bring the book and that QR code to their nearest USPS, UPS, or FedEx store "
        f"and show it to the associate there to ship it back, no printer needed. Refund will "
        f"be processed within 5-7 business days after the item is received."
    )


def cancel_order(order_number: str, verified_email: str) -> str:
    """Cancels an order before it ships, if it hasn't shipped yet.

    Deterministically checks: the order belongs to the verified customer, and
    its shipping_status is still "Processing" (not yet shipped). Once an order
    has shipped, it can no longer be cancelled — the customer needs a return
    instead (use initiate_return).

    Args:
        order_number: The order number, e.g. "BK-10001".
        verified_email: The email address that was just confirmed via
            verify_auth_code.

    Returns:
        Confirmation the order was cancelled, or an explanation of why it can't be.
    """
    orders = _read_tab("Orders!A1:K1000")
    row_index, order_row = None, None
    for i, row in enumerate(orders):
        if row.get("order_number", "").strip().lower() == order_number.strip().lower():
            row_index, order_row = i + 2, row
            break

    if order_row is None:
        return f"No order found with order number {order_number}."
    if order_row.get("customer_email", "").strip().lower() != verified_email.strip().lower():
        return (
            f"Order {order_number} does not belong to the verified account "
            f"({verified_email}). Access denied."
        )
    status = order_row.get("shipping_status", "").strip()
    if status == "Cancelled":
        return f"Order {order_number} ({order_row.get('book_ordered')}) is already cancelled."
    if status != "Processing":
        return (
            f"Order {order_number} ({order_row.get('book_ordered')}) has already shipped "
            f"(status: {status}) and can no longer be cancelled. Use initiate_return instead "
            f"if the customer wants to send it back."
        )

    _sheets.update(
        spreadsheetId=_SPREADSHEET_ID,
        range=f"Orders!C{row_index}",
        valueInputOption="RAW",
        body={"values": [["Cancelled"]]},
    ).execute()
    return (
        f"Order {order_number} ({order_row.get('book_ordered')}) has been cancelled. No charge "
        f"will be made and nothing will ship."
    )


def send_auth_code(email: str) -> str:
    """Sends a one-time verification code to the customer's email via Stytch.

    Use this to verify a customer's identity before looking up order-specific
    or account-specific details, or before resetting their password.

    Args:
        email: The customer's account email address.

    Returns:
        Confirmation the code was sent, an error message, or a lockout message
        if this email has already failed verification MAX_OTP_ATTEMPTS times —
        in that case, escalate_to_human instead of retrying.
    """
    key = email.strip().lower()
    if _failed_otp_attempts.get(key, 0) >= MAX_OTP_ATTEMPTS:
        return (
            f"This email has failed verification {MAX_OTP_ATTEMPTS} times and is locked "
            f"for this session. Do not send another code — escalate to a human instead."
        )
    try:
        resp = _stytch_client.otps.email.login_or_create(email=email)
    except StytchError as e:
        return f"Failed to send verification code: {e}"
    _pending_otps[key] = resp.email_id
    return f"A 6-digit verification code was sent to {email}. Ask the customer for it."


def verify_auth_code(email: str, code: str) -> str:
    """Verifies the one-time code the customer received via send_auth_code.

    Deterministically locks out further attempts for this email after
    MAX_OTP_ATTEMPTS consecutive failures, regardless of what the model does —
    at that point, escalate_to_human instead of retrying or sending a new code.

    Args:
        email: The customer's account email address (must match what was used
            in send_auth_code).
        code: The 6-digit code the customer provided.

    Returns:
        Whether verification succeeded. Only treat the customer as identity-verified
        if this says success.
    """
    key = email.strip().lower()
    if _failed_otp_attempts.get(key, 0) >= MAX_OTP_ATTEMPTS:
        return (
            f"This email has failed verification {MAX_OTP_ATTEMPTS} times and is locked "
            f"for this session. Escalate to a human instead of retrying."
        )
    email_id = _pending_otps.get(key)
    if not email_id:
        return "No verification code was sent to this email yet. Call send_auth_code first."
    try:
        _stytch_client.otps.authenticate(method_id=email_id, code=code)
    except StytchError as e:
        _failed_otp_attempts[key] = _failed_otp_attempts.get(key, 0) + 1
        if _failed_otp_attempts[key] >= MAX_OTP_ATTEMPTS:
            _pending_otps.pop(key, None)
            return (
                f"Verification failed: {e}. This was attempt {_failed_otp_attempts[key]} of "
                f"{MAX_OTP_ATTEMPTS} — no attempts remain. Escalate to a human now."
            )
        return f"Verification failed: {e}"
    del _pending_otps[key]
    _failed_otp_attempts.pop(key, None)
    return "Verification succeeded. The customer's identity is confirmed."


def escalate_to_human(summary: str, reason: str) -> str:
    """Hands the conversation off to a human support agent.

    Only escalate when truly necessary: the customer explicitly asks for a
    human, identity verification has failed twice, or the situation isn't
    covered by the SOP or policy (e.g. fraud suspicion, VIP account, large
    order). Before escalating, try to resolve the issue yourself and offer
    to keep helping — don't escalate just because a request is complex.

    Args:
        summary: A brief summary of the conversation and what the customer needs.
        reason: Why this is being escalated.

    Returns:
        Confirmation the escalation was logged.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(_ESCALATION_LOG, "a") as f:
        f.write(f"[{timestamp}] REASON: {reason}\nSUMMARY: {summary}\n\n")
    return "Escalated to a human support agent, who will follow up on this conversation shortly."
