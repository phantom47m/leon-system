"""
Leon CRM — Client Relationship Management

Tracks leads, clients, deals, and the entire sales pipeline.
Leon uses this to know the state of every business relationship.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.business.crm")


class CRM:
    """
    Full CRM system for Leon.
    Tracks leads → prospects → clients → projects → revenue.
    """

    def __init__(self, data_file: str = "data/crm.json"):
        self.data_file = Path(data_file)
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        logger.info(
            f"CRM loaded: {len(self.data.get('leads', []))} leads, "
            f"{len(self.data.get('clients', []))} clients"
        )

    def _load(self) -> dict:
        if self.data_file.exists():
            try:
                return json.load(open(self.data_file))
            except json.JSONDecodeError:
                pass
        return {
            "leads": [],
            "clients": [],
            "deals": [],
            "interactions": [],
            "pipeline_stages": [
                "new",
                "contacted",
                "responded",
                "proposal_sent",
                "negotiating",
                "won",
                "lost",
            ],
        }

    def save(self):
        with open(self.data_file, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    # ══════════════════════════════════════════════════════
    # LEADS
    # ══════════════════════════════════════════════════════

    def add_lead(self, lead: dict) -> str:
        """Add a new lead to the CRM."""
        lead_id = uuid.uuid4().hex[:10]
        lead["id"] = lead_id
        lead["created_at"] = datetime.now().isoformat()
        lead["stage"] = lead.get("stage", "new")
        lead["follow_ups"] = []
        lead["emails_sent"] = 0
        lead["last_contact"] = None

        # Deduplicate by name + location
        for existing in self.data["leads"]:
            if (
                existing["name"].lower() == lead["name"].lower()
                and existing.get("location", "").lower() == lead.get("location", "").lower()
            ):
                logger.debug(f"Duplicate lead skipped: {lead['name']}")
                return existing["id"]

        self.data["leads"].append(lead)
        self.save()
        logger.info(f"New lead added: {lead['name']} (score: {lead.get('lead_score', '?')})")
        return lead_id

    def get_lead(self, lead_id: str) -> Optional[dict]:
        for lead in self.data["leads"]:
            if lead["id"] == lead_id:
                return lead
        return None

    def update_lead(self, lead_id: str, updates: dict):
        """Update a lead's info."""
        for lead in self.data["leads"]:
            if lead["id"] == lead_id:
                lead.update(updates)
                self.save()
                return True
        return False

    def advance_lead(self, lead_id: str, new_stage: str):
        """Move a lead to the next pipeline stage."""
        stages = self.data["pipeline_stages"]
        if new_stage not in stages:
            logger.warning(f"Invalid stage: {new_stage}")
            return

        for lead in self.data["leads"]:
            if lead["id"] == lead_id:
                old_stage = lead["stage"]
                lead["stage"] = new_stage
                lead[f"stage_{new_stage}_at"] = datetime.now().isoformat()
                self.save()
                logger.info(f"Lead {lead['name']}: {old_stage} → {new_stage}")

                # If won, convert to client
                if new_stage == "won":
                    self.convert_to_client(lead)
                return True
        return False

    def get_leads_by_stage(self, stage: str) -> list:
        return [l for l in self.data["leads"] if l.get("stage") == stage]

    def get_hot_leads(self, min_score: int = 70) -> list:
        return sorted(
            [l for l in self.data["leads"] if l.get("lead_score", 0) >= min_score],
            key=lambda x: x.get("lead_score", 0),
            reverse=True,
        )

    def get_leads_needing_followup(self, days_since_contact: int = 3) -> list:
        """Get leads that haven't been contacted recently."""
        cutoff = datetime.now() - timedelta(days=days_since_contact)
        results = []
        for lead in self.data["leads"]:
            if lead["stage"] in ("new", "contacted", "responded", "proposal_sent"):
                last = lead.get("last_contact")
                if not last or datetime.fromisoformat(last) < cutoff:
                    results.append(lead)
        return results

    # ══════════════════════════════════════════════════════
    # CLIENTS
    # ══════════════════════════════════════════════════════

    def convert_to_client(self, lead: dict) -> str:
        """Convert a won lead into a client."""
        client_id = uuid.uuid4().hex[:10]
        client = {
            "id": client_id,
            "name": lead["name"],
            "contact_info": {
                "phone": lead.get("phone", ""),
                "email": lead.get("email", ""),
                "address": lead.get("address", ""),
            },
            "original_lead_id": lead["id"],
            "created_at": datetime.now().isoformat(),
            "projects": [],
            "total_revenue": 0,
            "total_invoiced": 0,
            "total_paid": 0,
            "notes": lead.get("notes", ""),
            "satisfaction": None,
        }
        self.data["clients"].append(client)
        self.save()
        logger.info(f"New client: {client['name']}")
        return client_id

    def add_client(self, name: str, email: str = "", phone: str = "") -> str:
        """Manually add a client."""
        client_id = uuid.uuid4().hex[:10]
        client = {
            "id": client_id,
            "name": name,
            "contact_info": {"phone": phone, "email": email, "address": ""},
            "created_at": datetime.now().isoformat(),
            "projects": [],
            "total_revenue": 0,
            "total_invoiced": 0,
            "total_paid": 0,
            "notes": "",
        }
        self.data["clients"].append(client)
        self.save()
        return client_id

    def get_client(self, client_id: str) -> Optional[dict]:
        for c in self.data["clients"]:
            if c["id"] == client_id:
                return c
        return None

    def list_clients(self) -> list:
        return self.data.get("clients", [])

    # ══════════════════════════════════════════════════════
    # DEALS
    # ══════════════════════════════════════════════════════

    def add_deal(self, client_id: str, title: str, amount: float, service_type: str = "website") -> str:
        """Add a deal/project to a client."""
        deal_id = uuid.uuid4().hex[:10]
        deal = {
            "id": deal_id,
            "client_id": client_id,
            "title": title,
            "amount": amount,
            "service_type": service_type,
            "stage": "proposal",  # proposal → in_progress → delivered → paid
            "created_at": datetime.now().isoformat(),
            "deadline": None,
            "paid_amount": 0,
            "invoices": [],
        }
        self.data["deals"].append(deal)

        # Update client
        for c in self.data["clients"]:
            if c["id"] == client_id:
                c["projects"].append(deal_id)
                c["total_invoiced"] += amount
                break

        self.save()
        logger.info(f"New deal: {title} (${amount})")
        return deal_id

    def get_active_deals(self) -> list:
        return [d for d in self.data["deals"] if d["stage"] not in ("paid", "cancelled")]

    def record_payment(self, deal_id: str, amount: float):
        """Record a payment received."""
        for deal in self.data["deals"]:
            if deal["id"] == deal_id:
                deal["paid_amount"] += amount

                if deal["paid_amount"] >= deal["amount"]:
                    deal["stage"] = "paid"

                # Update client revenue
                for c in self.data["clients"]:
                    if c["id"] == deal["client_id"]:
                        c["total_paid"] += amount
                        c["total_revenue"] += amount
                        break

                self.save()
                logger.info(f"Payment recorded: ${amount} for {deal['title']}")
                return True
        return False

    # ══════════════════════════════════════════════════════
    # INTERACTIONS LOG
    # ══════════════════════════════════════════════════════

    def log_interaction(self, entity_id: str, interaction_type: str, summary: str):
        """Log any interaction (email, call, meeting, etc.)."""
        entry = {
            "id": uuid.uuid4().hex[:10],
            "entity_id": entity_id,
            "type": interaction_type,  # email, call, meeting, proposal, invoice
            "summary": summary,
            "timestamp": datetime.now().isoformat(),
        }
        self.data["interactions"].append(entry)

        # Update last_contact on leads
        for lead in self.data["leads"]:
            if lead["id"] == entity_id:
                lead["last_contact"] = datetime.now().isoformat()
                if interaction_type == "email":
                    lead["emails_sent"] = lead.get("emails_sent", 0) + 1
                break

        self.save()

    def get_interactions(self, entity_id: str) -> list:
        return [i for i in self.data["interactions"] if i["entity_id"] == entity_id]

    # ══════════════════════════════════════════════════════
    # PIPELINE OVERVIEW
    # ══════════════════════════════════════════════════════

    def get_pipeline_summary(self) -> dict:
        """Full pipeline overview for Leon's daily briefing."""
        stages = {}
        for stage in self.data["pipeline_stages"]:
            leads = self.get_leads_by_stage(stage)
            stages[stage] = {
                "count": len(leads),
                "leads": [{"name": l["name"], "score": l.get("lead_score", 0)} for l in leads[:5]],
            }

        active_deals = self.get_active_deals()
        total_pipeline_value = sum(d["amount"] for d in active_deals)
        total_paid = sum(d["paid_amount"] for d in self.data["deals"])

        return {
            "pipeline_stages": stages,
            "total_leads": len(self.data["leads"]),
            "total_clients": len(self.data["clients"]),
            "active_deals": len(active_deals),
            "pipeline_value": total_pipeline_value,
            "total_revenue": total_paid,
            "needs_followup": len(self.get_leads_needing_followup()),
        }
