"""
Leon Lead Generation — Find clients who need websites/apps automatically.

Strategies:
1. Google Maps scraping — find local businesses with bad/no websites
2. Freelance platforms — monitor Upwork, Fiverr for relevant gigs
3. Social media — find people asking for web dev help
4. Cold outreach — automated personalized emails to prospects
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.business.leads")


class LeadGenerator:
    """
    Autonomous lead generation engine.
    Finds businesses that need websites/apps and adds them to the CRM.
    """

    def __init__(self, crm, api_client, config: dict = None):
        self.crm = crm
        self.api = api_client
        self.config = config or {}
        self.data_dir = Path("data/leads")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Lead sources
        self.services_offered = [
            "website design",
            "web development",
            "mobile app development",
            "e-commerce websites",
            "business websites",
            "React development",
            "full-stack development",
        ]

        logger.info("Lead generator initialized")

    # ══════════════════════════════════════════════════════
    # GOOGLE MAPS — LOCAL BUSINESS SCRAPING
    # ══════════════════════════════════════════════════════

    async def find_local_businesses(self, location: str, business_type: str, radius_miles: int = 25):
        """
        Find local businesses that have bad or no websites.

        Uses Google Maps / Places API to find businesses, then checks
        if their website is outdated, broken, or missing.

        Args:
            location: City/area to search (e.g. "Tampa, FL")
            business_type: Type of business (e.g. "restaurants", "contractors")
            radius_miles: Search radius
        """
        logger.info(f"Searching for {business_type} near {location}...")

        google_api_key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not google_api_key:
            logger.warning("GOOGLE_MAPS_API_KEY not set — using AI-powered search instead")
            return await self._ai_powered_lead_search(location, business_type)

        import aiohttp

        # Search Google Places
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": f"{business_type} in {location}",
            "key": google_api_key,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()

        leads = []
        for place in data.get("results", []):
            place_id = place.get("place_id")

            # Get details (including website)
            detail_url = "https://maps.googleapis.com/maps/api/place/details/json"
            detail_params = {
                "place_id": place_id,
                "fields": "name,formatted_address,formatted_phone_number,website,rating,user_ratings_total",
                "key": google_api_key,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(detail_url, params=detail_params) as resp:
                    detail = await resp.json()

            result = detail.get("result", {})
            website = result.get("website", "")

            # Score the lead
            score = await self._score_lead(result, website)

            if score >= 50:  # Only keep decent leads
                lead = {
                    "name": result.get("name", "Unknown"),
                    "address": result.get("formatted_address", ""),
                    "phone": result.get("formatted_phone_number", ""),
                    "website": website,
                    "rating": result.get("rating", 0),
                    "review_count": result.get("user_ratings_total", 0),
                    "lead_score": score,
                    "source": "google_maps",
                    "business_type": business_type,
                    "location": location,
                    "found_at": datetime.now().isoformat(),
                    "status": "new",
                    "notes": self._generate_lead_notes(result, website, score),
                }
                leads.append(lead)

                # Add to CRM
                self.crm.add_lead(lead)

        logger.info(f"Found {len(leads)} qualified leads for {business_type} in {location}")
        return leads

    async def _score_lead(self, business: dict, website: str) -> int:
        """
        Score a lead 0-100 based on how much they need our services.

        High score = they really need a website/app
        """
        score = 50  # Base score

        # No website at all = HOT lead
        if not website:
            score += 40
            return min(score, 100)

        # Has website — check quality
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(website, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status >= 400:
                        score += 30  # Broken website
                    else:
                        html = await resp.text()

                        # Check for signs of a bad website
                        if "wordpress" in html.lower() and "theme" in html.lower():
                            score += 10  # Generic WordPress
                        if "wix" in html.lower() or "squarespace" in html.lower():
                            score += 5  # Website builder (could be upgraded)
                        if "copyright 2020" in html.lower() or "copyright 2019" in html.lower():
                            score += 20  # Outdated
                        if not self._is_mobile_responsive(html):
                            score += 15  # Not mobile friendly
                        if "http://" in website and "https://" not in website:
                            score += 10  # No SSL

        except Exception:
            score += 25  # Website unreachable

        # High ratings but few reviews = established but not digital
        rating = business.get("rating", 0)
        reviews = business.get("user_ratings_total", 0)
        if rating >= 4.0 and reviews < 50:
            score += 10  # Good business, weak online presence

        return min(score, 100)

    def _is_mobile_responsive(self, html: str) -> bool:
        """Quick check if website has responsive meta tag."""
        return "viewport" in html.lower()

    def _generate_lead_notes(self, business: dict, website: str, score: int) -> str:
        """Generate human-readable notes about why this is a good lead."""
        notes = []
        if not website:
            notes.append("NO WEBSITE — needs one built from scratch")
        elif score >= 70:
            notes.append("Website is outdated or poorly built — strong upgrade opportunity")
        if business.get("rating", 0) >= 4.0:
            notes.append(f"Good reputation ({business.get('rating')}/5 stars)")
        return "; ".join(notes) if notes else "Potential lead"

    # ══════════════════════════════════════════════════════
    # AI-POWERED LEAD SEARCH (no API key needed)
    # ══════════════════════════════════════════════════════

    async def _ai_powered_lead_search(self, location: str, business_type: str) -> list:
        """
        Use Claude to research and find leads when no Google API key.
        Leon uses his own brain to find prospects.
        """
        prompt = f"""Find 10 real {business_type} businesses in {location} that likely need
a better website or don't have one. For each, provide:

1. Business name
2. What they do
3. Why they might need a website (no website, outdated, bad design, etc.)
4. Estimated lead quality (1-10)

Focus on businesses that:
- Have no website or a very basic one
- Are clearly established (Google reviews, been around a while)
- Could benefit from professional web design/development

Return as JSON array:
[{{"name": "...", "business_type": "...", "reason": "...", "score": 8}}]"""

        response = await self.api.quick_request(prompt)

        try:
            # Parse JSON from response
            text = response.strip()
            if "```" in text:
                text = text.split("```json")[-1].split("```")[0] if "```json" in text else text.split("```")[1].split("```")[0]
            leads_data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Could not parse AI lead search results")
            return []

        leads = []
        for item in leads_data:
            lead = {
                "name": item.get("name", "Unknown"),
                "business_type": item.get("business_type", business_type),
                "address": f"{location}",
                "phone": "",
                "website": "",
                "lead_score": item.get("score", 5) * 10,
                "source": "ai_research",
                "location": location,
                "found_at": datetime.now().isoformat(),
                "status": "new",
                "notes": item.get("reason", ""),
            }
            leads.append(lead)
            self.crm.add_lead(lead)

        return leads

    # ══════════════════════════════════════════════════════
    # FREELANCE PLATFORM MONITORING
    # ══════════════════════════════════════════════════════

    async def monitor_freelance_platforms(self):
        """
        Monitor Upwork, Fiverr, and other platforms for relevant gigs.
        Uses web scraping via Playwright.
        """
        logger.info("Scanning freelance platforms for opportunities...")

        opportunities = []

        # Use AI to search and analyze opportunities
        prompt = f"""Search for recent freelance opportunities that match these services:
{json.dumps(self.services_offered)}

Look for:
- Upwork job posts for web development
- Fiverr buyer requests
- Reddit posts looking for web developers
- Facebook groups posting web dev jobs

For each opportunity found, provide:
- Platform
- Title/description
- Budget (if mentioned)
- How to apply
- Match score (1-10)

Return as JSON array."""

        response = await self.api.quick_request(prompt)
        # Store opportunities for review
        self._save_opportunities(response)

        return opportunities

    def _save_opportunities(self, data: str):
        """Save found opportunities to disk."""
        filepath = self.data_dir / f"opportunities_{datetime.now().strftime('%Y%m%d')}.json"
        with open(filepath, "w") as f:
            f.write(data)

    # ══════════════════════════════════════════════════════
    # AUTOMATED OUTREACH
    # ══════════════════════════════════════════════════════

    async def generate_outreach_email(self, lead: dict) -> dict:
        """
        Generate a personalized cold outreach email for a lead.
        """
        prompt = f"""Write a short, professional cold email to this business:

Business: {lead['name']}
Type: {lead.get('business_type', 'local business')}
Location: {lead.get('location', '')}
Current website situation: {lead.get('notes', 'unknown')}

You are reaching out to offer web design/development services.

Rules:
- Keep it under 150 words
- Be personalized (mention their specific business)
- Focus on VALUE (more customers, better online presence)
- Include a clear call to action
- Professional but friendly tone
- Don't be pushy or salesy
- Mention you can show examples of your work

Return JSON:
{{"subject": "...", "body": "..."}}"""

        response = await self.api.quick_request(prompt)
        try:
            text = response.strip()
            if "```" in text:
                text = text.split("```json")[-1].split("```")[0] if "```json" in text else text.split("```")[1].split("```")[0]
            email = json.loads(text)
            return email
        except json.JSONDecodeError:
            return {"subject": "Quick question about your online presence", "body": response}

    async def generate_proposal(self, lead: dict, service_type: str = "website") -> str:
        """
        Generate a full proposal document for a qualified lead.
        """
        prompt = f"""Create a professional project proposal for:

Client: {lead['name']}
Service: {service_type}
Their situation: {lead.get('notes', 'Needs a new website')}

Include these sections:
1. Executive Summary (why they need this)
2. Proposed Solution (what we'll build)
3. Features & Deliverables (bullet points)
4. Timeline (realistic estimates)
5. Investment (price ranges for different tiers)
   - Basic: $500-1500
   - Professional: $1500-3500
   - Premium: $3500-7500
6. Why Choose Us
7. Next Steps

Make it professional, compelling, and personalized to their business.
Format as clean markdown."""

        proposal = await self.api.quick_request(prompt)
        return proposal

    # ══════════════════════════════════════════════════════
    # AUTONOMOUS LEAD HUNT (background)
    # ══════════════════════════════════════════════════════

    async def run_daily_lead_hunt(self, locations: list = None, business_types: list = None):
        """
        Run a full daily lead hunting session.
        Can be scheduled to run automatically.
        """
        locations = locations or ["Tampa, FL"]
        business_types = business_types or [
            "restaurants",
            "contractors",
            "plumbers",
            "dentists",
            "real estate agents",
            "auto repair shops",
            "hair salons",
            "gyms",
            "lawyers",
            "accountants",
        ]

        logger.info(f"Starting daily lead hunt: {len(locations)} locations, {len(business_types)} types")

        all_leads = []
        for location in locations:
            for btype in business_types:
                try:
                    leads = await self.find_local_businesses(location, btype)
                    all_leads.extend(leads)
                    await asyncio.sleep(2)  # Rate limiting
                except Exception as e:
                    logger.error(f"Error searching {btype} in {location}: {e}")

        # Also check freelance platforms
        await self.monitor_freelance_platforms()

        # Generate daily report
        report = self._generate_lead_report(all_leads)
        logger.info(f"Daily lead hunt complete: {len(all_leads)} new leads found")

        return report

    def _generate_lead_report(self, leads: list) -> dict:
        """Generate summary report of found leads."""
        hot = [l for l in leads if l.get("lead_score", 0) >= 70]
        warm = [l for l in leads if 40 <= l.get("lead_score", 0) < 70]
        cold = [l for l in leads if l.get("lead_score", 0) < 40]

        return {
            "date": datetime.now().isoformat(),
            "total_leads": len(leads),
            "hot_leads": len(hot),
            "warm_leads": len(warm),
            "cold_leads": len(cold),
            "top_leads": sorted(leads, key=lambda x: x.get("lead_score", 0), reverse=True)[:5],
            "summary": f"Found {len(leads)} leads: {len(hot)} hot, {len(warm)} warm, {len(cold)} cold",
        }
