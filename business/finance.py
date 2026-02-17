"""
Leon Finance — Revenue tracking, invoicing, expenses, and reporting.

"Leon, how much did I make this month?"
"You made $4,200 this month. 3 invoices paid, 2 pending ($1,800 outstanding)."
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.business.finance")


class FinanceTracker:
    """
    Tracks all money in and out. Generates invoices. Reports revenue.
    """

    def __init__(self, data_file: str = "data/finance.json"):
        self.data_file = Path(data_file)
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        logger.info("Finance tracker initialized")

    def _load(self) -> dict:
        if self.data_file.exists():
            try:
                with open(self.data_file) as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass
        return {
            "invoices": [],
            "payments": [],
            "expenses": [],
            "recurring": [],
        }

    def save(self):
        with open(self.data_file, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    # ══════════════════════════════════════════════════════
    # INVOICES
    # ══════════════════════════════════════════════════════

    def create_invoice(
        self,
        client_name: str,
        client_email: str,
        items: list,
        due_days: int = 30,
        notes: str = "",
    ) -> dict:
        """
        Create a new invoice.

        Args:
            items: [{"description": "Website Design", "amount": 2500.00}, ...]
        """
        invoice_id = f"INV-{datetime.now().strftime('%Y%m')}-{uuid.uuid4().hex[:4].upper()}"
        total = sum(item["amount"] for item in items)
        due_date = (datetime.now() + timedelta(days=due_days)).isoformat()

        invoice = {
            "id": invoice_id,
            "client_name": client_name,
            "client_email": client_email,
            "items": items,
            "total": total,
            "status": "draft",  # draft → sent → paid → overdue
            "created_at": datetime.now().isoformat(),
            "due_date": due_date,
            "sent_at": None,
            "paid_at": None,
            "paid_amount": 0,
            "notes": notes,
        }

        self.data["invoices"].append(invoice)
        self.save()
        logger.info(f"Invoice created: {invoice_id} — ${total} for {client_name}")
        return invoice

    def mark_invoice_sent(self, invoice_id: str):
        for inv in self.data["invoices"]:
            if inv["id"] == invoice_id:
                inv["status"] = "sent"
                inv["sent_at"] = datetime.now().isoformat()
                self.save()
                return True
        return False

    def mark_invoice_paid(self, invoice_id: str, amount: float = None):
        for inv in self.data["invoices"]:
            if inv["id"] == invoice_id:
                inv["status"] = "paid"
                inv["paid_at"] = datetime.now().isoformat()
                inv["paid_amount"] = amount or inv["total"]

                # Log payment
                self.data["payments"].append({
                    "id": uuid.uuid4().hex[:10],
                    "invoice_id": invoice_id,
                    "client": inv["client_name"],
                    "amount": inv["paid_amount"],
                    "date": datetime.now().isoformat(),
                    "type": "invoice_payment",
                })

                self.save()
                logger.info(f"Invoice {invoice_id} paid: ${inv['paid_amount']}")
                return True
        return False

    def get_overdue_invoices(self) -> list:
        now = datetime.now()
        overdue = []
        for inv in self.data["invoices"]:
            if inv["status"] == "sent":
                due = datetime.fromisoformat(inv["due_date"])
                if now > due:
                    inv["status"] = "overdue"
                    overdue.append(inv)
        if overdue:
            self.save()
        return overdue

    def get_pending_invoices(self) -> list:
        return [i for i in self.data["invoices"] if i["status"] in ("sent", "overdue")]

    def generate_invoice_html(self, invoice_id: str) -> str:
        """Generate a clean HTML invoice for sending to clients."""
        inv = None
        for i in self.data["invoices"]:
            if i["id"] == invoice_id:
                inv = i
                break
        if not inv:
            return ""

        items_html = ""
        for item in inv["items"]:
            items_html += f"""
            <tr>
                <td style="padding:10px;border-bottom:1px solid #eee">{item['description']}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;text-align:right">${item['amount']:,.2f}</td>
            </tr>"""

        return f"""
<!DOCTYPE html>
<html>
<head><style>
    body {{ font-family: 'Helvetica', sans-serif; color: #333; max-width: 700px; margin: 0 auto; padding: 40px; }}
    .header {{ display: flex; justify-content: space-between; margin-bottom: 40px; }}
    .invoice-title {{ font-size: 28px; font-weight: bold; color: #2c3e50; }}
    .invoice-id {{ color: #7f8c8d; font-size: 14px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
    .total-row {{ font-size: 20px; font-weight: bold; }}
    .footer {{ margin-top: 40px; font-size: 12px; color: #95a5a6; }}
</style></head>
<body>
    <div class="header">
        <div>
            <div class="invoice-title">INVOICE</div>
            <div class="invoice-id">{inv['id']}</div>
        </div>
        <div style="text-align:right">
            <div><strong>Date:</strong> {inv['created_at'][:10]}</div>
            <div><strong>Due:</strong> {inv['due_date'][:10]}</div>
        </div>
    </div>

    <div style="margin-bottom:30px">
        <strong>Bill To:</strong><br>
        {inv['client_name']}<br>
        {inv['client_email']}
    </div>

    <table>
        <tr style="background:#f8f9fa">
            <th style="padding:10px;text-align:left">Description</th>
            <th style="padding:10px;text-align:right">Amount</th>
        </tr>
        {items_html}
        <tr class="total-row">
            <td style="padding:15px">TOTAL</td>
            <td style="padding:15px;text-align:right">${inv['total']:,.2f}</td>
        </tr>
    </table>

    {"<p><strong>Notes:</strong> " + inv['notes'] + "</p>" if inv['notes'] else ""}

    <div class="footer">
        <p>Thank you for your business!</p>
        <p>Payment terms: Net {(datetime.fromisoformat(inv['due_date']) - datetime.fromisoformat(inv['created_at'])).days} days</p>
    </div>
</body>
</html>"""

    # ══════════════════════════════════════════════════════
    # EXPENSES
    # ══════════════════════════════════════════════════════

    def add_expense(self, description: str, amount: float, category: str = "business"):
        """Track an expense."""
        expense = {
            "id": uuid.uuid4().hex[:10],
            "description": description,
            "amount": amount,
            "category": category,  # business, software, hosting, marketing, etc.
            "date": datetime.now().isoformat(),
        }
        self.data["expenses"].append(expense)
        self.save()
        logger.info(f"Expense logged: ${amount} — {description}")

    # ══════════════════════════════════════════════════════
    # REVENUE REPORTING
    # ══════════════════════════════════════════════════════

    def get_revenue_report(self, period: str = "month") -> dict:
        """
        Generate revenue report.

        Args:
            period: "today", "week", "month", "year", "all"
        """
        now = datetime.now()

        if period == "today":
            cutoff = now.replace(hour=0, minute=0, second=0)
        elif period == "week":
            cutoff = now - timedelta(days=7)
        elif period == "month":
            cutoff = now.replace(day=1, hour=0, minute=0, second=0)
        elif period == "year":
            cutoff = now.replace(month=1, day=1, hour=0, minute=0, second=0)
        else:
            cutoff = datetime.min

        # Revenue (payments received)
        payments = [
            p for p in self.data["payments"]
            if datetime.fromisoformat(p["date"]) >= cutoff
        ]
        total_revenue = sum(p["amount"] for p in payments)

        # Expenses
        expenses = [
            e for e in self.data["expenses"]
            if datetime.fromisoformat(e["date"]) >= cutoff
        ]
        total_expenses = sum(e["amount"] for e in expenses)

        # Pending (invoiced but not paid)
        pending = self.get_pending_invoices()
        total_pending = sum(i["total"] - i.get("paid_amount", 0) for i in pending)

        # Overdue
        overdue = self.get_overdue_invoices()
        total_overdue = sum(i["total"] - i.get("paid_amount", 0) for i in overdue)

        return {
            "period": period,
            "revenue": total_revenue,
            "expenses": total_expenses,
            "profit": total_revenue - total_expenses,
            "pending_invoices": len(pending),
            "pending_amount": total_pending,
            "overdue_invoices": len(overdue),
            "overdue_amount": total_overdue,
            "payments": payments,
            "expense_breakdown": self._expense_breakdown(expenses),
        }

    def _expense_breakdown(self, expenses: list) -> dict:
        """Break down expenses by category."""
        breakdown = {}
        for e in expenses:
            cat = e.get("category", "other")
            breakdown[cat] = breakdown.get(cat, 0) + e["amount"]
        return breakdown

    def get_daily_summary(self) -> str:
        """
        Quick daily financial summary for Leon's briefing.

        Returns human-readable string.
        """
        today = self.get_revenue_report("today")
        month = self.get_revenue_report("month")
        pending = self.get_pending_invoices()

        lines = [
            f"💰 Today: ${today['revenue']:,.2f} revenue",
            f"📊 This month: ${month['revenue']:,.2f} revenue, ${month['expenses']:,.2f} expenses",
            f"📈 Monthly profit: ${month['profit']:,.2f}",
            f"📋 Pending invoices: {len(pending)} (${month['pending_amount']:,.2f})",
        ]

        if month["overdue_invoices"] > 0:
            lines.append(f"⚠️ OVERDUE: {month['overdue_invoices']} invoices (${month['overdue_amount']:,.2f})")

        return "\n".join(lines)
