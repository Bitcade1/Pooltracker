"""Read-only Google Sheets access and validation for invoice order columns."""

import calendar
from datetime import date, datetime, timezone
import re
from urllib.parse import quote


class InvoiceSyncError(ValueError):
    pass


def column_letter(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def collection_identity(label, month, year):
    """Use the month heading to disambiguate the source's mixed US/UK dates."""
    values = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?", str(label).strip())
    if not values:
        raise InvoiceSyncError(f"Unrecognised collection date: {label}. Use DD/MM under a month heading.")
    first, second = int(values[1]), int(values[2])
    if second == month:
        day = first
    elif first == month:
        day = second
    else:
        raise InvoiceSyncError(f"Collection date {label} does not match its month heading.")
    if values[3]:
        year = int(values[3])
        if year < 100:
            year += 2000
    try:
        value = date(year, month, day)
    except ValueError as exc:
        raise InvoiceSyncError(f"Invalid collection date: {label}.") from exc
    return value.isoformat(), value.strftime("%d/%m")


def source_order_date(order, source):
    season = re.search(r"(?<!\d)(\d{2}|\d{4})/\d{2,4}(?!\d)", source["source_title"])
    if not season:
        raise InvoiceSyncError("The spreadsheet title must include its season, for example 26/27.")
    year = int(season[1])
    if year < 100:
        year += 2000
    # This workbook runs August through March.
    month = list(calendar.month_name).index(order["month"])
    if month < 8:
        year += 1
    return collection_identity(order["collection_date"], month, year)


def source_order_identity(order, source):
    if order.get("order_id"):
        return "id:" + order["order_id"]
    return "date:" + source_order_date(order, source)[0]


def parse_order_values(values, source):
    def cell(row, col):
        return str(values[row][col]).strip() if row < len(values) and col < len(values[row]) else ""

    header = next((r for r in range(min(20, len(values)))
                   if cell(r, 0).lower() == "model" and cell(r, 1).lower() == "sku"), None)
    if header is None or header == 0:
        raise InvoiceSyncError("Orders must have a Model / Sku header beneath its month headings.")
    total_row = next((r for r in range(header + 3, len(values)) if cell(r, 0).lower() == "total"), None)
    if total_row is None:
        raise InvoiceSyncError("The Orders Total row was not found. No invoices were changed.")
    products, product_rows = [], {}
    group = ""
    order_id_row = None
    for row in range(header + 3, total_row):
        label, sku = cell(row, 0), cell(row, 1)
        if label.lower() == "order id":
            order_id_row = row
        elif sku:
            if not label or sku in product_rows:
                raise InvoiceSyncError("Every product needs a description and a unique SKU.")
            product_rows[sku] = row
            products.append({"sku": sku, "description": " ".join(part for part in (group, label) if part)})
        elif label:
            group = label
    if not products:
        raise InvoiceSyncError("No product rows were found. No invoices were changed.")
    invoices, identities, warnings = [], set(), []
    known_columns = {order["source_column"] for order in source["invoices"]}
    month = ""
    for col in range(2, max(map(len, values), default=0)):
        month = cell(header - 1, col) or month
        label = cell(header, col)
        column = column_letter(col + 1)
        quantities_present = any(cell(row, col) not in ("", "0", "0.0") for row in product_rows.values())
        if not label:
            if quantities_present:
                if column in known_columns:
                    raise InvoiceSyncError(f"Column {column} has quantities but no collection date.")
                warnings.append({"column": column, "kind": "incomplete_order",
                                 "message": f"Column {column} has quantities but no delivery date yet. It will be imported when its date is added."})
            continue
        if month not in calendar.month_name:
            raise InvoiceSyncError(f"Column {column_letter(col + 1)} needs a full month heading.")
        quantities = {}
        for sku, row in product_rows.items():
            quantity = cell(row, col) or "0"
            if not re.fullmatch(r"\d+(?:\.0+)?", quantity) or len(quantity) > 10:
                raise InvoiceSyncError(f"Quantity at {column_letter(col + 1)}{row + 1} must be a non-negative whole number.")
            quantities[sku] = int(quantity.split(".")[0])
        heading_text = cell(header + 1, col)
        heading = re.fullmatch(r"(\d+)\s+Tables?", heading_text, re.I)
        if heading:
            expected_quantity = int(heading[1])
        elif not heading_text:
            if not any(quantities.values()) and column not in known_columns:
                warnings.append({"column": column, "kind": "incomplete_order",
                                 "message": f"Column {column} has a date but no table count or ordered items yet. It has not been imported."})
                continue
            expected_quantity = sum(quantities.values())
            warnings.append({"column": column, "kind": "inferred_table_count",
                             "message": f"Column {column}: the table-count heading is blank, so {expected_quantity} tables were calculated from its item rows."})
        else:
            raise InvoiceSyncError(f"Column {column_letter(col + 1)} needs a table-count heading, for example 35 Tables.")
        order = {
            "source_column": column_letter(col + 1), "collection_date": label, "month": month,
            "reference": cell(header + 2, col) or "TBC", "expected_quantity": expected_quantity,
            "quantities": quantities,
        }
        if order_id_row is not None:
            order_id = cell(order_id_row, col)
            if not order_id or len(order_id) > 80:
                raise InvoiceSyncError("Every dated column needs a unique Order ID when the Order ID row is present.")
            order["order_id"] = order_id
        identity = source_order_identity(order, source)
        if identity in identities:
            raise InvoiceSyncError("Duplicate collection dates or Order IDs. Add unique values in an Order ID row below the invoice references.")
        identities.add(identity)
        _, order["collection_date"] = source_order_date(order, source)
        invoices.append(order)
    if not invoices:
        raise InvoiceSyncError("No dated order columns were found. Existing invoices were kept.")
    return dict(source, products=products, invoices=invoices, sync_warnings=warnings,
                imported_on=datetime.now(timezone.utc).date().isoformat())


def fetch_invoice_source(source, credentials_path):
    if not credentials_path:
        raise InvoiceSyncError("Google connection is not configured. Set POOLTRACKER_SHEETS_CREDENTIALS on the server.")
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    except (ImportError, OSError, ValueError) as exc:
        raise InvoiceSyncError("Could not load the Google read-only credentials. Check the server credentials file and google-auth installation.") from exc
    spreadsheet_id = source["source_url"].split("/d/")[1].split("/")[0]
    endpoint = "https://sheets.googleapis.com/v4/spreadsheets/" + quote(spreadsheet_id, safe="")
    try:
        with AuthorizedSession(credentials, refresh_timeout=20) as connection:
            response = connection.get(endpoint, params={"fields": "properties(title),sheets(properties)"}, timeout=(10, 30))
            if response.status_code != 200:
                raise InvoiceSyncError(f"Google returned HTTP {response.status_code}. Enable the Sheets API and share this spreadsheet with the service account as Viewer.")
            metadata = response.json()
            sheet = next((sheet["properties"] for sheet in metadata["sheets"]
                          if sheet["properties"]["title"] == source["source_tab"]), None)
            if sheet is None:
                raise InvoiceSyncError("The Orders tab is missing from the connected spreadsheet.")
            columns = sheet["gridProperties"]["columnCount"]
            if columns > 256:
                raise InvoiceSyncError("Orders has more than 256 columns; narrow the sheet before syncing.")
            rows = min(sheet["gridProperties"]["rowCount"], 200, 50000 // max(columns, 1))
            tab = source["source_tab"].replace("'", "''")
            target_range = f"'{tab}'!A1:{column_letter(columns)}{rows}"
            response = connection.get(endpoint + "/values/" + quote(target_range, safe=""),
                                      params={"valueRenderOption": "FORMATTED_VALUE"}, timeout=(10, 30))
            if response.status_code != 200:
                raise InvoiceSyncError(f"Google returned HTTP {response.status_code} while reading Orders. Existing invoices were kept.")
            return parse_order_values(response.json().get("values", []), dict(source, source_title=metadata["properties"]["title"]))
    except InvoiceSyncError:
        raise
    except Exception as exc:
        # Do not persist provider responses, credentials, or access tokens in UI/logs.
        raise InvoiceSyncError("Google Sheets could not be reached or returned invalid data. Existing invoices were kept; the next scheduled run will retry.") from exc
