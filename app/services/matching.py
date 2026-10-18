"""Deterministic, conservative canonical-business match decisions."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from math import asin, cos, radians, sin, sqrt

from app.db.models import Business
from app.schemas import Candidate, normalize_domain, normalize_name
from app.services.normalization import normalize_address, normalize_phone


class MatchClass(StrEnum):
    EXACT = "exact"
    LIKELY = "likely"
    UNCERTAIN = "uncertain"
    DISTINCT = "distinct"


@dataclass(frozen=True)
class MatchingRules:
    likely_threshold: float = 0.78
    uncertain_threshold: float = 0.48

    @classmethod
    def from_config(cls, config: dict | None) -> "MatchingRules":
        config = config or {}
        try:
            likely = float(config.get("likely_threshold", 0.78))
            uncertain = float(config.get("uncertain_threshold", 0.48))
        except (TypeError, ValueError):
            return cls()
        if not 0 <= uncertain < likely <= 1:
            return cls()
        return cls(likely_threshold=likely, uncertain_threshold=uncertain)


@dataclass(frozen=True)
class MatchResult:
    classification: MatchClass
    rule: str
    score: float
    candidate: Business | None


def _distance_meters(a: Business | Candidate, b: Candidate) -> float | None:
    if a.latitude is None or a.longitude is None or b.latitude is None or b.longitude is None:
        return None
    lat1, lat2 = radians(a.latitude), radians(b.latitude)
    dlat = lat2 - lat1
    dlon = radians(b.longitude - a.longitude)
    haversine = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6_371_000 * 2 * asin(sqrt(haversine))


def classify_candidate(
    business: Business, candidate: Candidate, rules: MatchingRules | None = None
) -> MatchResult:
    rules = rules or MatchingRules()
    name_key = normalize_name(candidate.name)
    address_key = normalize_address(candidate.address)
    phone_key = normalize_phone(candidate.phone)
    domain_key = normalize_domain(candidate.website)
    name_ratio = SequenceMatcher(None, business.normalized_name or "", name_key).ratio()
    same_address = bool(address_key and business.normalized_address == address_key)
    same_phone = bool(phone_key and business.normalized_phone == phone_key)
    same_domain = bool(domain_key and business.domain == domain_key)
    same_city = bool(
        candidate.city
        and business.city
        and candidate.city.casefold().strip() == business.city.casefold().strip()
    )
    distance = _distance_meters(business, candidate)

    # Conflicting location evidence is a branch boundary, even when the domain/name match.
    if business.normalized_address and address_key and business.normalized_address != address_key:
        return MatchResult(MatchClass.DISTINCT, "different_address", 0.0, business)
    if (
        business.city
        and candidate.city
        and business.city.casefold().strip() != candidate.city.casefold().strip()
    ):
        return MatchResult(MatchClass.DISTINCT, "different_city", 0.0, business)
    if distance is not None and distance > 1_000:
        return MatchResult(MatchClass.DISTINCT, "different_location", 0.0, business)

    if name_ratio == 1.0 and same_address:
        return MatchResult(MatchClass.EXACT, "exact_name_address", 1.0, business)
    if (
        same_domain
        and (same_address or (distance is not None and distance <= 50))
        and name_ratio >= 0.65
    ):
        return MatchResult(MatchClass.EXACT, "domain_name_same_location", 1.0, business)

    components: list[tuple[float, float]] = [(name_ratio, 0.50)]
    if domain_key and business.domain:
        components.append((float(same_domain), 0.25))
    if phone_key and business.normalized_phone:
        components.append((float(same_phone), 0.20))
    if address_key and business.normalized_address:
        components.append((float(same_address), 0.25))
    if candidate.city and business.city:
        components.append((float(same_city), 0.10))
    if distance is not None:
        components.append((1.0 if distance <= 50 else 0.0, 0.20))
    score = sum(value * weight for value, weight in components) / sum(
        weight for _, weight in components
    )
    has_corroborating_evidence = bool(
        (domain_key and same_domain)
        or (address_key and same_address)
        or (phone_key and same_phone)
        or (candidate.city and business.city and same_city)
        or distance is not None
    )
    if not has_corroborating_evidence:
        score = min(score, rules.uncertain_threshold)
    if score >= rules.likely_threshold:
        classification, rule = MatchClass.LIKELY, "weighted_similarity_likely"
    elif score >= rules.uncertain_threshold:
        classification, rule = MatchClass.UNCERTAIN, "weighted_similarity_uncertain"
    else:
        classification, rule = MatchClass.DISTINCT, "below_match_threshold"
    return MatchResult(classification, rule, round(score, 4), business)


def best_match(
    businesses: list[Business], candidate: Candidate, rules: MatchingRules | None = None
) -> MatchResult:
    if not businesses:
        return MatchResult(MatchClass.DISTINCT, "no_candidate", 0.0, None)
    results = [classify_candidate(business, candidate, rules) for business in businesses]
    results.sort(
        key=lambda result: (-result.score, result.candidate.id if result.candidate else "")
    )
    if len(results) > 1 and results[0].score == results[1].score and results[0].score > 0:
        return MatchResult(
            MatchClass.UNCERTAIN, "ambiguous_multiple_candidates", results[0].score, None
        )
    return results[0]
