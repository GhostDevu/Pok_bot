from __future__ import annotations

"""
Competitive Sneak Peek Hold'em bot for IIT Pokerbots 2026.

Design goals:
- Always return legal actions.
- Stay under the stricter documented 20s total time bank, even though the local
  reference engine initializes 30s.
- Use a GTO-ish baseline with exploitative adjustments.
- Leverage the Sneak Peek auction with second-price style bidding.
- Keep all logic in a single file.
"""

from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot

import math
import random
import time
from functools import lru_cache
from itertools import combinations

try:
    import eval7  # Competition dependency.
except Exception:  # pragma: no cover - fallback for environments without eval7.
    print("eval7 not available, using slower fallback evaluator.")
    eval7 = None

try:
    from treys import Card as TreysCard, Evaluator as TreysEvaluator
    _TREYS_EVALUATOR = TreysEvaluator()
    _treys_available = True
except Exception:  # pragma: no cover
    _treys_available = False
    _TREYS_EVALUATOR = None


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
STARTING_STACK = 5000
SMALL_BLIND = 10
BIG_BLIND = 20
NUM_ROUNDS = 1000

# Official docs say 20s total, local engine code currently starts a 30s clock.
# We target the stricter documented limit.
SOFT_TOTAL_TIME = 19.5
PER_ACTION_HARD_CAP = 1.80

RANKS = "23456789TJQKA"
SUITS = "cdhs"
RANK_TO_INT = {r: i + 2 for i, r in enumerate(RANKS)}
INT_TO_RANK = {v: k for k, v in RANK_TO_INT.items()}
FULL_DECK = [r + s for r in RANKS for s in SUITS]


# -----------------------------------------------------------------------------
# Card / evaluation helpers
# -----------------------------------------------------------------------------
@lru_cache(maxsize=64)
def _card_obj(card: str):
    if eval7 is None:
        return card
    return eval7.Card(card)


def _rank(card: str) -> int:
    return RANK_TO_INT[card[0]]


def _suit(card: str) -> str:
    return card[1]


def _normalize_street(street: str) -> str:
    if street in ("preflop", "pre-flop"):
        return "pre-flop"
    return street


def _effective_stack(state: PokerState) -> int:
    return min(state.my_chips, state.opp_chips)


def _remaining_cards(excluded: set[str]) -> list[str]:
    return [c for c in FULL_DECK if c not in excluded]


# ----- fallback evaluator (used only if eval7 is unavailable) -----------------
def _straight_high(ranks_desc: list[int]) -> int:
    uniq = sorted(set(ranks_desc), reverse=True)
    if 14 in uniq:
        uniq.append(1)
    run = 1
    best = 0
    for i in range(len(uniq) - 1):
        if uniq[i] - 1 == uniq[i + 1]:
            run += 1
            if run >= 5:
                best = max(best, uniq[i - 3])
        elif uniq[i] != uniq[i + 1]:
            run = 1
    return best


def _eval_five(cards: tuple[str, ...]) -> tuple:
    ranks = sorted((_rank(c) for c in cards), reverse=True)
    suits = [_suit(c) for c in cards]
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    freq = sorted(counts.values(), reverse=True)

    flush = len(set(suits)) == 1
    sh = _straight_high(ranks)
    straight = sh > 0

    if flush and straight:
        return (8, sh)
    if freq[0] == 4:
        four = groups[0][0]
        kicker = max(r for r, c in counts.items() if c == 1)
        return (7, four, kicker)
    if freq[0] == 3 and freq[1] == 2:
        return (6, groups[0][0], groups[1][0])
    if flush:
        return (5,) + tuple(sorted(ranks, reverse=True))
    if straight:
        return (4, sh)
    if freq[0] == 3:
        trips = groups[0][0]
        kickers = sorted((r for r, c in counts.items() if c == 1), reverse=True)
        return (3, trips) + tuple(kickers)
    if freq[0] == 2 and freq[1] == 2:
        pairs = sorted((r for r, c in counts.items() if c == 2), reverse=True)
        kicker = max(r for r, c in counts.items() if c == 1)
        return (2, pairs[0], pairs[1], kicker)
    if freq[0] == 2:
        pair = groups[0][0]
        kickers = sorted((r for r, c in counts.items() if c == 1), reverse=True)
        return (1, pair) + tuple(kickers)
    return (0,) + tuple(sorted(ranks, reverse=True))


@lru_cache(maxsize=200000)
def _best_rank_fallback(cards_key: tuple[str, ...]) -> tuple:
    cards = list(cards_key)
    best = None
    for combo in combinations(cards, 5):
        val = _eval_five(combo)
        if best is None or val > best:
            best = val
    return best


@lru_cache(maxsize=200000)
def _best_rank_eval7(cards_key: tuple[str, ...]):
    card_objs = [_card_obj(c) for c in cards_key]
    return eval7.evaluate(card_objs)


def _best_rank(cards: list[str]):
    key = tuple(sorted(cards))
    if eval7 is not None:
        return _best_rank_eval7(key)
    if _treys_available:
        return _best_rank_treys(key)
    return _best_rank_fallback(key)


@lru_cache(maxsize=200000)
def _best_rank_treys(cards_key: tuple[str, ...]) -> int:
    """
    Use treys to evaluate the best 5-card hand from cards_key.
    Returns an integer where LOWER = STRONGER (treys convention).
    Wrapped to match the comparison direction of _best_rank_fallback by negating.
    We store raw treys score; _compare_hands_treys handles direction.
    """
    cards = list(cards_key)
    # treys expects Card objects
    treys_cards = []
    for c in cards:
        # Convert rank: treys uses same rank chars but lowercase suits except 's' for spades
        # Our suits: c=clubs, d=diamonds, h=hearts, s=spades — same as treys
        treys_cards.append(TreysCard.new(c))
    if len(treys_cards) == 5:
        return _TREYS_EVALUATOR.evaluate([], treys_cards)
    elif len(treys_cards) == 6:
        best = None
        for combo in combinations(range(6), 5):
            five = [treys_cards[i] for i in combo]
            score = _TREYS_EVALUATOR.evaluate([], five)
            if best is None or score < best:
                best = score
        return best
    elif len(treys_cards) == 7:
        board = treys_cards[:5]
        hole = treys_cards[5:]
        return _TREYS_EVALUATOR.evaluate(board, hole)
    # Fallback for unusual counts
    best = None
    for combo in combinations(range(len(treys_cards)), 5):
        five = [treys_cards[i] for i in combo]
        score = _TREYS_EVALUATOR.evaluate([], five)
        if best is None or score < best:
            best = score
    return best if best is not None else 9999


def _compare_hands(cards_a: list[str], cards_b: list[str]) -> int:
    va = _best_rank(cards_a)
    vb = _best_rank(cards_b)
    if eval7 is None and _treys_available:
        # treys: lower score = stronger hand
        return (va < vb) - (va > vb)
    return (va > vb) - (va < vb)


# -----------------------------------------------------------------------------
# Hand features
# -----------------------------------------------------------------------------
def preflop_strength(hand: list[str]) -> float:
    """Approximate HU preflop strength in [0,1]."""
    if len(hand) != 2:
        return 0.5
    r1, r2 = _rank(hand[0]), _rank(hand[1])
    hi, lo = max(r1, r2), min(r1, r2)
    suited = _suit(hand[0]) == _suit(hand[1])
    gap = hi - lo
    pair = hi == lo

    # Chen-ish base scoring.
    if pair:
        base = 6 + 2.2 * hi
        if hi <= 5:
            base += 1.0
    else:
        base = 0.60 * hi + 0.34 * lo
        if hi >= 13:
            base += 1.8
        elif hi >= 11:
            base += 1.0
        if suited:
            base += 1.7
        if gap == 0:
            base += 1.2
        elif gap == 1:
            base += 0.8
        elif gap == 2:
            base += 0.2
        elif gap == 3:
            base -= 0.7
        else:
            base -= 1.6
        if hi <= 6:
            base -= 0.5
        if hi <= 8 and gap <= 1:
            base += 0.6

    # Normalize to [0,1].
    score = (base - 2.0) / 33.0
    return max(0.02, min(0.99, score))


def board_texture(board: list[str]) -> dict:
    if not board:
        return {
            "paired": False,
            "monotone": False,
            "two_tone": False,
            "connected": False,
            "very_wet": False,
            "high_card": 0,
        }
    ranks = sorted((_rank(c) for c in board), reverse=True)
    uniq = sorted(set(ranks))
    suits = [_suit(c) for c in board]
    suit_counts = {s: suits.count(s) for s in set(suits)}
    max_suit = max(suit_counts.values())
    gaps = []
    for i in range(len(uniq) - 1):
        gaps.append(uniq[i + 1] - uniq[i])
    connected = False
    if gaps:
        connected = min(gaps) <= 2 and (max(uniq) - min(uniq) <= 5)

    paired = len(uniq) < len(ranks)
    monotone = max_suit >= 3
    two_tone = max_suit == 2
    broadways = sum(1 for r in ranks if r >= 11)
    very_wet = monotone or (connected and two_tone) or (paired and two_tone)
    return {
        "paired": paired,
        "monotone": monotone,
        "two_tone": two_tone,
        "connected": connected,
        "very_wet": very_wet,
        "broadways": broadways,
        "high_card": max(ranks),
    }


def made_hand_info(hand: list[str], board: list[str]) -> dict:
    cards = hand + board
    ranks = [_rank(c) for c in cards]
    suits = [_suit(c) for c in cards]
    rank_counts = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1
    counts_sorted = sorted(rank_counts.values(), reverse=True)

    # Straight / flush across 5-7 cards.
    flush = False
    flush_suit = None
    for s in set(suits):
        if suits.count(s) >= 5:
            flush = True
            flush_suit = s
            break
    straight = _straight_high(sorted(ranks, reverse=True)) > 0

    category = 0
    if flush and straight:
        category = 8
    elif counts_sorted and counts_sorted[0] == 4:
        category = 7
    elif len(counts_sorted) >= 2 and counts_sorted[0] == 3 and counts_sorted[1] >= 2:
        category = 6
    elif flush:
        category = 5
    elif straight:
        category = 4
    elif counts_sorted and counts_sorted[0] == 3:
        category = 3
    elif len(counts_sorted) >= 2 and counts_sorted[0] == 2 and counts_sorted[1] == 2:
        category = 2
    elif counts_sorted and counts_sorted[0] == 2:
        category = 1

    board_ranks = [_rank(c) for c in board] if board else []
    top_pair = False
    second_pair = False
    overpair = False
    pocket_pair = len(hand) == 2 and _rank(hand[0]) == _rank(hand[1])
    if board_ranks and category <= 2:
        max_board = max(board_ranks)
        uniq_board = sorted(set(board_ranks), reverse=True)
        hole_ranks = [_rank(c) for c in hand]
        if pocket_pair and hole_ranks[0] > max_board:
            overpair = True
        if any(hr == max_board for hr in hole_ranks):
            top_pair = True
        elif len(uniq_board) >= 2 and any(hr == uniq_board[1] for hr in hole_ranks):
            second_pair = True

    return {
        "category": category,
        "top_pair": top_pair,
        "second_pair": second_pair,
        "overpair": overpair,
        "pocket_pair": pocket_pair,
        "flush": flush,
        "flush_suit": flush_suit,
        "straight": straight,
    }


def draw_info(hand: list[str], board: list[str]) -> dict:
    cards = hand + board
    suits = [_suit(c) for c in cards]
    ranks = sorted(set(_rank(c) for c in cards))
    flush_draw = False
    nut_flush_draw = False
    for s in set(suits):
        cnt = suits.count(s)
        if cnt == 4 and len(board) < 5:
            flush_draw = True
            suited_hole = [c for c in hand if _suit(c) == s]
            if suited_hole:
                best_hole = max(_rank(c) for c in suited_hole)
                if best_hole == 14:
                    nut_flush_draw = True
    # Straight draws.
    ext = set(ranks)
    if 14 in ext:
        ext.add(1)
    oesd = False
    gutshot = False
    for start in range(1, 11):
        window = {start + i for i in range(5)}
        hits = len(window & ext)
        if hits >= 4:
            if window <= ext:
                continue
            missing = list(window - ext)
            if len(missing) == 1:
                miss = missing[0]
                if miss in (start, start + 4):
                    oesd = True
                else:
                    gutshot = True
    return {
        "flush_draw": flush_draw,
        "nut_flush_draw": nut_flush_draw,
        "oesd": oesd,
        "gutshot": gutshot,
        "combo_draw": flush_draw and (oesd or gutshot),
    }


def pot_odds_threshold(cost_to_call: int, pot: int) -> float:
    if cost_to_call <= 0:
        return 0.0
    denom = pot + cost_to_call
    if denom <= 0:
        return 1.0
    return cost_to_call / denom


def minimum_defense_fraction(cost_to_call: int, pot: int) -> float:
    # Approximate MDF against a bet of size ~= cost_to_call.
    if cost_to_call <= 0:
        return 1.0
    bet = cost_to_call
    if pot + bet <= 0:
        return 0.0
    return pot / (pot + bet)


def stack_to_pot_ratio(state: PokerState) -> float:
    pot = max(1, state.pot)
    return _effective_stack(state) / pot


# -----------------------------------------------------------------------------
# Equity engine
# -----------------------------------------------------------------------------
class EquityEngine:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.cache: dict[tuple, float] = {}

    def clear_hand_cache(self) -> None:
        self.cache.clear()

    def equity(
        self,
        hand: list[str],
        board: list[str],
        opp_known: list[str],
        approx_budget: float,
        villain_tightness: float,
    ) -> float:
        key = (
            tuple(sorted(hand)),
            tuple(board),
            tuple(sorted(opp_known)),
            len(board),
            round(villain_tightness, 2),
        )
        if key in self.cache:
            return self.cache[key]

        if len(board) == 5:
            val = self._river_equity(hand, board, opp_known, approx_budget, villain_tightness)
            self.cache[key] = val
            return val

        val = self._sample_equity(hand, board, opp_known, approx_budget, villain_tightness)
        self.cache[key] = val
        return val

    def _river_equity(
        self,
        hand: list[str],
        board: list[str],
        opp_known: list[str],
        approx_budget: float,
        villain_tightness: float,
    ) -> float:
        known = set(hand) | set(board) | set(opp_known)
        rem = _remaining_cards(known)
        wins = 0.0
        total = 0.0
        my_cards = hand + board

        if len(opp_known) == 1:
            for c in rem:
                opp = opp_known + [c]
                w = self._combo_weight(opp, villain_tightness)
                total += w
                cmpv = _compare_hands(my_cards, opp + board)
                if cmpv > 0:
                    wins += w
                elif cmpv == 0:
                    wins += 0.5 * w
        else:
            # Exact enumeration is usually still cheap here (<= 990 combos).
            if len(rem) <= 48 and approx_budget >= 0.002:
                for i in range(len(rem)):
                    for j in range(i + 1, len(rem)):
                        opp = [rem[i], rem[j]]
                        w = self._combo_weight(opp, villain_tightness)
                        total += w
                        cmpv = _compare_hands(my_cards, opp + board)
                        if cmpv > 0:
                            wins += w
                        elif cmpv == 0:
                            wins += 0.5 * w
            else:
                samples = max(24, min(80, int(approx_budget * 4000)))
                for _ in range(samples):
                    opp = self.rng.sample(rem, 2)
                    w = self._combo_weight(opp, villain_tightness)
                    total += w
                    cmpv = _compare_hands(my_cards, opp + board)
                    if cmpv > 0:
                        wins += w
                    elif cmpv == 0:
                        wins += 0.5 * w
        return wins / total if total > 0 else 0.5

    def _sample_equity(
        self,
        hand: list[str],
        board: list[str],
        opp_known: list[str],
        approx_budget: float,
        villain_tightness: float,
    ) -> float:
        known = set(hand) | set(board) | set(opp_known)
        rem = _remaining_cards(known)
        board_to_come = 5 - len(board)
        opp_unknown = 2 - len(opp_known)

        # Very light sampling to fit the tight match time bank.
        # When treys is available as evaluator it's faster, allow more samples.
        _speed_mult = 1.8 if (_treys_available and eval7 is None) else 1.0
        if len(board) == 0:
            # Preflop MC: run a small number of full runouts.
            _speed_mult = 1.8 if (_treys_available and eval7 is None) else 1.0
            samples = max(0, min(int(40 * _speed_mult), int(approx_budget * 1800 * _speed_mult)))
        elif len(board) == 3:
            samples = max(18, min(int(60 * _speed_mult), int(approx_budget * 2200 * _speed_mult)))
        elif len(board) == 4:
            samples = max(18, min(int(72 * _speed_mult), int(approx_budget * 2600 * _speed_mult)))
        else:
            samples = max(16, min(int(40 * _speed_mult), int(approx_budget * 1800 * _speed_mult)))

        if samples <= 0:
            return preflop_strength(hand)

        wins = 0.0
        total = 0.0
        for _ in range(samples):
            draw = self.rng.sample(rem, board_to_come + opp_unknown)
            runout = board + draw[:board_to_come]
            opp = opp_known + draw[board_to_come:]
            weight = self._combo_weight(opp, villain_tightness)
            total += weight
            cmpv = _compare_hands(hand + runout, opp + runout)
            if cmpv > 0:
                wins += weight
            elif cmpv == 0:
                wins += 0.5 * weight
        return wins / total if total > 0 else 0.5

    @staticmethod
    def _combo_weight(opp_cards: list[str], villain_tightness: float) -> float:
        """
        Crude range weighting: tighter villains are slightly more likely to hold
        stronger preflop combinations in unknown-card simulations.
        """
        if len(opp_cards) != 2:
            return 1.0
        s = preflop_strength(opp_cards)
        # villain_tightness in [0,1], 1 = very tight. Blend towards strong combos.
        bias = 0.55 + 0.90 * villain_tightness
        return max(0.30, 1.0 + bias * (s - 0.50))


# -----------------------------------------------------------------------------
# Opponent model
# -----------------------------------------------------------------------------
class OpponentModel:
    def __init__(self):
        self.hands = 0
        self.vpip = 0.0
        self.preflop_raise = 0.0
        self.aggressive_actions = 0.0
        self.calls = 0.0
        self.fold_to_our_aggression = 0.0
        self.faced_our_aggression = 0.0
        self.showdowns = 0.0
        self.showdown_wins = 0.0
        self.exact_auction_bids: list[int] = []
        self.auction_lower_bounds: list[int] = []
        self.recent_payoffs: list[int] = []

    def decay(self) -> None:
        # Light decay keeps adaptation responsive across 1000 rounds.
        for attr in (
            "vpip",
            "preflop_raise",
            "aggressive_actions",
            "calls",
            "fold_to_our_aggression",
            "faced_our_aggression",
            "showdowns",
            "showdown_wins",
        ):
            setattr(self, attr, getattr(self, attr) * 0.9975)
        if len(self.exact_auction_bids) > 160:
            self.exact_auction_bids = self.exact_auction_bids[-160:]
        if len(self.auction_lower_bounds) > 160:
            self.auction_lower_bounds = self.auction_lower_bounds[-160:]
        if len(self.recent_payoffs) > 80:
            self.recent_payoffs = self.recent_payoffs[-80:]

    def on_hand_start(self) -> None:
        self.hands += 1
        self.decay()

    def record_vpip(self) -> None:
        self.vpip += 1.0

    def record_preflop_raise(self) -> None:
        self.preflop_raise += 1.0
        self.aggressive_actions += 1.0

    def record_aggressive(self) -> None:
        self.aggressive_actions += 1.0

    def record_call(self) -> None:
        self.calls += 1.0

    def record_fold_to_our_aggression(self) -> None:
        self.fold_to_our_aggression += 1.0
        self.faced_our_aggression += 1.0

    def record_survived_our_aggression(self) -> None:
        self.faced_our_aggression += 1.0

    def record_showdown(self, opp_won: bool) -> None:
        self.showdowns += 1.0
        if opp_won:
            self.showdown_wins += 1.0

    def record_auction_exact(self, bid: int) -> None:
        if bid >= 0:
            self.exact_auction_bids.append(int(bid))

    def record_auction_lower_bound(self, bid_lb: int) -> None:
        if bid_lb > 0:
            self.auction_lower_bounds.append(int(bid_lb))

    def record_payoff(self, delta: int) -> None:
        self.recent_payoffs.append(delta)

    def vpip_rate(self) -> float:
        return self.vpip / max(1.0, min(self.hands, 120.0))

    def pfr_rate(self) -> float:
        return self.preflop_raise / max(1.0, min(self.hands, 120.0))

    def aggression_factor(self) -> float:
        if self.calls <= 0.2:
            return 2.5 if self.aggressive_actions > 0 else 1.0
        return min(6.0, self.aggressive_actions / self.calls)

    def fold_to_aggression_rate(self) -> float:
        if self.faced_our_aggression < 1.0:
            return 0.38
        return max(0.08, min(0.85, self.fold_to_our_aggression / self.faced_our_aggression))

    def showdown_win_rate(self) -> float:
        if self.showdowns < 1.0:
            return 0.50
        return self.showdown_wins / self.showdowns

    def villain_tightness(self) -> float:
        # 0 = very loose, 1 = very tight.
        vp = self.vpip_rate()
        pf = self.pfr_rate()
        raw = 0.60 - 0.90 * vp + 0.35 * pf
        return max(0.0, min(1.0, raw))

    def estimated_auction_bid(self) -> float:
        """Mean estimate in chips."""
        if self.exact_auction_bids:
            exact_mean = sum(self.exact_auction_bids[-40:]) / min(40, len(self.exact_auction_bids))
        else:
            exact_mean = 140.0
        if self.auction_lower_bounds:
            lb = sum(self.auction_lower_bounds[-20:]) / min(20, len(self.auction_lower_bounds))
            return max(exact_mean, 0.85 * lb)
        return exact_mean

    def is_loose(self) -> bool:
        return self.vpip_rate() > 0.60

    def is_tight(self) -> bool:
        return self.vpip_rate() < 0.38

    def is_aggressive(self) -> bool:
        return self.aggression_factor() > 1.8

    def is_passive(self) -> bool:
        return self.aggression_factor() < 0.9


# -----------------------------------------------------------------------------
# Player
# -----------------------------------------------------------------------------
class Player(BaseBot):
    def __init__(self):
        self.rng = random.Random(20260301)
        self.eq = EquityEngine(self.rng)
        self.opp = OpponentModel()
        self.internal_time_used = 0.0

        # Hand state.
        self.last_seen_state: PokerState | None = None
        self.last_action_was_aggro = False
        self.last_aggressive_street: str | None = None
        self.preflop_opened = False
        self.preflop_raised_once = False
        self.hand_id = 0
        self.opp_vpip_this_hand = False
        self.opp_pfr_this_hand = False

        # Auction tracking.
        self.awaiting_auction_resolution = False
        self.last_bid_amount = 0
        self.pre_auction_my_chips = STARTING_STACK
        self.pre_auction_opp_chips = STARTING_STACK

    # ----- lifecycle ---------------------------------------------------------
    def on_hand_start(self, game_info: GameInfo, current_state: PokerState) -> None:
        self.hand_id += 1
        self.eq.clear_hand_cache()
        self.opp.on_hand_start()
        self.last_seen_state = current_state
        self.last_action_was_aggro = False
        self.last_aggressive_street = None
        self.preflop_opened = False
        self.preflop_raised_once = False
        self.awaiting_auction_resolution = False
        self.last_bid_amount = 0
        self.opp_vpip_this_hand = False
        self.opp_pfr_this_hand = False
        self.pre_auction_my_chips = current_state.my_chips
        self.pre_auction_opp_chips = current_state.opp_chips

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState) -> None:
        self._observe_terminal(current_state)
        self.opp.record_payoff(current_state.payoff)
        self.last_seen_state = None
        self.last_action_was_aggro = False
        self.last_aggressive_street = None
        self.awaiting_auction_resolution = False

    # ----- decision entry ----------------------------------------------------
    def get_move(self, game_info: GameInfo, current_state: PokerState):
        t0 = time.perf_counter()
        street = _normalize_street(current_state.street)
        budget = self._decision_budget(game_info)

        self._observe_transition(current_state)

        if street == "auction":
            move = self._auction_action(game_info, current_state, budget)
        elif street == "pre-flop":
            move = self._preflop_action(game_info, current_state, budget)
        elif street in ("flop", "turn"):
            move = self._postflop_action(game_info, current_state, budget)
        elif street == "river":
            move = self._river_action(game_info, current_state, budget)
        else:
            move = self._safe_action(current_state)

        if not self._is_legal(move, current_state):
            move = self._safe_action(current_state)

        # Update self-side action trackers.
        self._record_our_action(current_state, move)
        self.last_seen_state = current_state

        self.internal_time_used += time.perf_counter() - t0
        return move

    # ------------------------------------------------------------------
    # Observation / opponent inference
    # ------------------------------------------------------------------
    def _observe_transition(self, state: PokerState) -> None:
        prev = self.last_seen_state
        if prev is None:
            self.last_seen_state = state
            return

        # Resolve auction after the engine sends the N update.
        if self.awaiting_auction_resolution and _normalize_street(state.street) != "auction":
            self._resolve_auction_from_state(state)
            self.awaiting_auction_resolution = False

        prev_street = _normalize_street(prev.street)
        cur_street = _normalize_street(state.street)

        # Opponent wager changes since the last time we acted.
        if state.opp_wager > prev.opp_wager:
            delta = state.opp_wager - prev.opp_wager
            if state.opp_wager > state.my_wager:
                self.opp.record_aggressive()
                if cur_street == "pre-flop":
                    if not self.opp_pfr_this_hand:
                        self.opp.record_preflop_raise()
                        self.opp_pfr_this_hand = True
                    if not self.opp_vpip_this_hand:
                        self.opp.record_vpip()
                        self.opp_vpip_this_hand = True
            else:
                self.opp.record_call()
                if cur_street == "pre-flop" and state.opp_wager > BIG_BLIND and not self.opp_vpip_this_hand:
                    self.opp.record_vpip()
                    self.opp_vpip_this_hand = True

        # If street advanced after we applied aggression, villain continued.
        if prev_street == cur_street:
            pass
        else:
            if self.last_action_was_aggro and self.last_aggressive_street == prev_street:
                self.opp.record_survived_our_aggression()
            # Preflop VPIP inference on limp/check lines.
            if prev_street == "pre-flop" and prev.opp_wager > BIG_BLIND and not self.opp_vpip_this_hand:
                self.opp.record_vpip()
                self.opp_vpip_this_hand = True

        self.last_seen_state = state

    def _observe_terminal(self, state: PokerState) -> None:
        # Showdown info arrives as opp_revealed_cards == both cards when wagers matched.
        showdown = len(state.opp_revealed_cards) >= 2
        if showdown:
            if self.last_action_was_aggro:
                self.opp.record_survived_our_aggression()
            self.opp.record_showdown(opp_won=state.payoff < 0)
        else:
            # If we were the last aggressor and won without showdown, credit a fold.
            if self.last_action_was_aggro and state.payoff > 0:
                self.opp.record_fold_to_our_aggression()

    def _resolve_auction_from_state(self, state: PokerState) -> None:
        my_paid = max(0, self.pre_auction_my_chips - state.my_chips)
        opp_paid = max(0, self.pre_auction_opp_chips - state.opp_chips)
        reveals = len(state.opp_revealed_cards)

        # If we see one card and both paid the same positive amount -> tie.
        if reveals == 1 and my_paid > 0 and opp_paid > 0 and my_paid == opp_paid:
            self.opp.record_auction_exact(opp_paid)
            return

        # If we won the auction, our payment equals their bid exactly.
        if reveals == 1 and my_paid > 0 and opp_paid == 0:
            self.opp.record_auction_exact(my_paid)
            return

        # If we lost, opponent paid our bid, so their bid was strictly larger.
        if reveals == 0 and opp_paid > 0:
            self.opp.record_auction_lower_bound(self.last_bid_amount + 1)
            return

        # Both zero is possible when both bid 0 and tie, but then we still see one card.
        if reveals == 1 and my_paid == 0 and opp_paid == 0:
            self.opp.record_auction_exact(0)

    def _record_our_action(self, state: PokerState, action) -> None:
        street = _normalize_street(state.street)
        if isinstance(action, ActionRaise):
            self.last_action_was_aggro = True
            self.last_aggressive_street = street
            if street == "pre-flop":
                if not self.preflop_opened:
                    self.preflop_opened = True
                self.preflop_raised_once = True
        elif isinstance(action, ActionBid):
            self.awaiting_auction_resolution = True
            self.last_bid_amount = action.amount
            self.pre_auction_my_chips = state.my_chips
            self.pre_auction_opp_chips = state.opp_chips
            self.last_action_was_aggro = False
        else:
            # Calls/checks do not make us the last aggressor.
            if isinstance(action, ActionCall):
                self.last_action_was_aggro = False

    # ------------------------------------------------------------------
    # Time management
    # ------------------------------------------------------------------
    def _decision_budget(self, game_info: GameInfo) -> float:
        rounds_left = max(1, NUM_ROUNDS - game_info.round_num + 1)
        soft_remaining = min(game_info.time_bank, max(0.2, SOFT_TOTAL_TIME - self.internal_time_used))
        # Typical hands generate roughly 2-3 queries per player.
        est_queries_left = max(40.0, rounds_left * 2.2)
        base = soft_remaining / est_queries_left
        return max(0.004, min(0.060, min(base, PER_ACTION_HARD_CAP)))

    # ------------------------------------------------------------------
    # Mixed strategy helper
    # ------------------------------------------------------------------
    def _mix(self, probability: float, state: PokerState, salt: int) -> bool:
        if probability <= 0.0:
            return False
        if probability >= 1.0:
            return True
        # Deterministic pseudo-randomness by state.
        h = 2166136261
        blob = f"{self.hand_id}|{state.street}|{''.join(sorted(state.my_hand))}|{''.join(state.board)}|{state.my_wager}|{state.opp_wager}|{salt}"
        for ch in blob:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        return (h / 4294967296.0) < probability

    # ------------------------------------------------------------------
    # Equity wrapper
    # ------------------------------------------------------------------
    def _equity(self, state: PokerState, budget: float) -> float:
        street = _normalize_street(state.street)
        if street == "pre-flop":
            # Blend heuristic with lightweight MC when a fast evaluator is available.
            if (_treys_available or eval7 is not None) and budget >= 0.012:
                mc_eq = self.eq.equity(
                    state.my_hand,
                    [],  # empty board = preflop
                    state.opp_revealed_cards,
                    budget * 0.40,  # use 40% of budget for this estimate
                    self.opp.villain_tightness(),
                )
                heuristic = preflop_strength(state.my_hand)
                # Blend: weight MC more as budget grows, but never abandon heuristic entirely
                mc_weight = min(0.65, budget * 12.0)
                return mc_weight * mc_eq + (1.0 - mc_weight) * heuristic
            return preflop_strength(state.my_hand)
        return self.eq.equity(
            state.my_hand,
            state.board,
            state.opp_revealed_cards,
            budget,
            self.opp.villain_tightness(),
        )

    # ------------------------------------------------------------------
    # Action builders
    # ------------------------------------------------------------------
    def _raise_to(self, state: PokerState, target: int) -> ActionRaise:
        mn, mx = state.raise_bounds
        amt = max(mn, min(mx, int(target)))
        return ActionRaise(amt)

    def _pot_raise(self, state: PokerState, fraction: float) -> ActionRaise:
        target = state.my_wager + int(max(1, state.pot) * fraction)
        return self._raise_to(state, target)

    def _safe_action(self, state: PokerState):
        if state.can_act(ActionCheck):
            return ActionCheck()
        if state.can_act(ActionCall):
            # Only take the autop-call when the price is genuinely cheap.
            if state.cost_to_call <= max(BIG_BLIND, int(0.12 * max(1, state.pot))):
                return ActionCall()
            return ActionFold()
        if state.can_act(ActionBid):
            return ActionBid(0)
        return ActionFold()

    def _is_legal(self, action, state: PokerState) -> bool:
        if isinstance(action, ActionFold):
            return state.can_act(ActionFold)
        if isinstance(action, ActionCheck):
            return state.can_act(ActionCheck)
        if isinstance(action, ActionCall):
            return state.can_act(ActionCall)
        if isinstance(action, ActionRaise):
            if not state.can_act(ActionRaise):
                return False
            mn, mx = state.raise_bounds
            return mn <= action.amount <= mx
        if isinstance(action, ActionBid):
            return state.can_act(ActionBid) and 0 <= action.amount <= state.my_chips
        return False

    # ------------------------------------------------------------------
    # Auction
    # ------------------------------------------------------------------
    def _auction_action(self, game_info: GameInfo, state: PokerState, budget: float):
        eq = self._equity(state, budget)
        pot = max(1, state.pot)
        eff = _effective_stack(state)
        uncertainty = 4.0 * eq * (1.0 - eq)  # peaks at 1 near 50/50.

        # More value when the pot is already meaningful and stacks remain deep.
        pot_component = 0.085 * pot
        stack_component = 0.018 * eff
        raw_value = uncertainty * (pot_component + stack_component)

        # Exploit / counter-exploit based on inferred opponent bids.
        opp_est = self.opp.estimated_auction_bid()
        if self.opp.is_aggressive():
            raw_value *= 1.08
        if self.opp.is_passive():
            raw_value *= 0.95

        # If we already have an extreme hand, information matters less.
        if eq <= 0.18 or eq >= 0.82:
            raw_value *= 0.38

        # We still want to compete around the opponent's typical bid if our value is close.
        bid = int(max(0.0, raw_value))
        if 0.45 <= eq <= 0.62 and bid < opp_est * 0.85:
            bid = int(0.85 * opp_est)
        if 0.38 <= eq <= 0.68 and self._mix(0.22, state, 901):
            bid = int(max(bid, opp_est + BIG_BLIND))

        # Never torch too much stack in a 1000-round bankroll contest.
        cap = min(state.my_chips, max(0, int(0.14 * STARTING_STACK + 0.03 * pot)))
        bid = max(0, min(cap, bid))

        # If opponent is consistently overbidding and the spot is low-value, opt out.
        if opp_est > 260 and uncertainty < 0.55 and bid < opp_est * 0.55:
            bid = 0

        return ActionBid(bid)

    # ------------------------------------------------------------------
    # Preflop
    # ------------------------------------------------------------------
    def _preflop_action(self, game_info: GameInfo, state: PokerState, budget: float):
        s = preflop_strength(state.my_hand)
        cost = state.cost_to_call
        pot = max(1, state.pot)
        is_bb = state.is_bb
        blocker = max(_rank(c) for c in state.my_hand) >= 13

        # Small blind: first to act preflop.
        if not is_bb:
            # Opening spot.
            if state.my_wager == SMALL_BLIND and cost > 0:
                if not state.can_act(ActionRaise):
                    # Can only call or fold (rare all-in/edge case).
                    if s >= 0.46 or (s >= 0.34 and cost <= BIG_BLIND):
                        return ActionCall()
                    return ActionFold()

                open_thresh = 0.30
                limp_thresh = 0.16
                if self.opp.is_tight():
                    open_thresh -= 0.04
                if self.opp.is_loose() and self.opp.is_aggressive():
                    open_thresh += 0.03
                    limp_thresh += 0.03

                if s >= 0.83:
                    # Premiums: bigger value open.
                    target = 90 if self._mix(0.55, state, 101) else 70
                    return self._raise_to(state, target)
                if s >= open_thresh:
                    # Wide HU opening range, mixed sizing.
                    target = 60
                    if s >= 0.64:
                        target = 80
                    elif s < 0.40 and self._mix(0.35, state, 102):
                        target = 40
                    if self._mix(0.82 if s >= 0.45 else 0.58, state, 103):
                        return self._raise_to(state, target)
                    return ActionCall()
                if s >= limp_thresh:
                    # Mix limp and fold for middling trash.
                    if self._mix(0.78, state, 104):
                        return ActionCall()
                    return ActionFold()
                if blocker and self.opp.fold_to_aggression_rate() > 0.46 and self._mix(0.10, state, 105):
                    return self._raise_to(state, 40)
                return ActionFold()

            # Facing a re-raise after we already entered the pot.
            thresh = pot_odds_threshold(cost, pot)
            if s >= 0.88 and state.can_act(ActionRaise):
                # Back-raise for value / jam if capped.
                jam_target = state.raise_bounds[1]
                if stack_to_pot_ratio(state) <= 2.0:
                    return ActionRaise(jam_target)
                return self._raise_to(state, int(state.my_wager + max(2 * cost, 0.85 * pot)))
            if s >= max(0.42, thresh + 0.06):
                return ActionCall()
            if blocker and s >= thresh and self._mix(0.08, state, 106):
                return ActionCall()
            return ActionFold()

        # Big blind.
        if cost == 0:
            # Facing an SB limp: punish limp with a merged range, keep some checks.
            if not state.can_act(ActionRaise):
                return ActionCheck()
            stab_freq = 0.0
            if s >= 0.74:
                stab_freq = 1.0
            elif s >= 0.54:
                stab_freq = 0.72
            elif s >= 0.40:
                stab_freq = 0.34
            elif blocker and self.opp.is_tight():
                stab_freq = 0.14
            if self._mix(stab_freq, state, 201):
                target = 80 if s >= 0.70 else 60
                return self._raise_to(state, target)
            return ActionCheck()

        # Facing an SB raise.
        thresh = pot_odds_threshold(cost, pot)
        fold_to_3b = self.opp.fold_to_aggression_rate()
        if s >= 0.86 and state.can_act(ActionRaise):
            # Strong 3-bet / 4-bet.
            target = min(state.raise_bounds[1], max(state.raise_bounds[0], int(state.my_wager + max(2.3 * cost, 0.70 * pot))))
            if stack_to_pot_ratio(state) <= 1.8:
                target = state.raise_bounds[1]
            return ActionRaise(target)
        if s >= max(0.34, thresh + 0.03):
            # Defend wide in HU.
            if s >= 0.60 and state.can_act(ActionRaise) and self._mix(0.12 if self.opp.is_aggressive() else 0.07, state, 202):
                target = min(state.raise_bounds[1], max(state.raise_bounds[0], int(state.my_wager + max(2.0 * cost, 0.60 * pot))))
                return ActionRaise(target)
            return ActionCall()
        if blocker and s >= max(0.27, thresh - 0.01) and state.can_act(ActionRaise) and fold_to_3b > 0.48 and self._mix(0.08, state, 203):
            target = min(state.raise_bounds[1], max(state.raise_bounds[0], int(state.my_wager + max(2.1 * cost, 0.60 * pot))))
            return ActionRaise(target)
        return ActionFold()

    # ------------------------------------------------------------------
    # Flop / Turn
    # ------------------------------------------------------------------


    def _postflop_action(self, game_info: GameInfo, state: PokerState, budget: float):
        street = _normalize_street(state.street)
        eq = self._equity(state, budget)
        made = made_hand_info(state.my_hand, state.board)
        draws = draw_info(state.my_hand, state.board)
        tex = board_texture(state.board)
        cost = state.cost_to_call
        pot = max(1, state.pot)
        spr = stack_to_pot_ratio(state)
        fold_rate = self.opp.fold_to_aggression_rate()
        is_first_to_act = state.is_bb  # BB acts first on all postflop streets in this engine.

        strong_made = made["category"] >= 3 or made["overpair"] or (made["top_pair"] and eq >= 0.62)
        medium_made = made["category"] >= 2 or made["top_pair"] or made["second_pair"]
        strong_draw = draws["combo_draw"] or draws["nut_flush_draw"] or (draws["flush_draw"] and draws["oesd"])
        # semi_bluff = strong_draw or (draws["flush_draw"] and eq >= 0.30) or (draws["oesd"] and eq >= 0.28)
        semi_bluff = False

        # Facing a bet / raise.
        if cost > 0:
            need = pot_odds_threshold(cost, pot)
            mdf = minimum_defense_fraction(cost, pot)
            # Slight anti-overfold floor in frequent small-bet spots.
            call_floor = 0.0
            if cost <= 0.33 * pot:
                call_floor = 0.02 + 0.03 * (mdf - 0.50)

            if strong_made and eq >= 0.66:
                if state.can_act(ActionRaise):
                    if spr <= 1.6:
                        return ActionRaise(state.raise_bounds[1])
                    frac = 0.95 if tex["very_wet"] else 0.70
                    return self._pot_raise(state, frac)
                return ActionCall()

            if semi_bluff and eq + 0.06 >= need:
                if state.can_act(ActionRaise) and fold_rate >= 0.34 and self._mix(0.26 if street == "flop" else 0.18, state, 301):
                    frac = 0.72 if tex["very_wet"] else 0.58
                    return self._pot_raise(state, frac)
                return ActionCall()

            if medium_made and eq + call_floor >= need:
                return ActionCall()

            if eq + call_floor >= need and cost <= 0.20 * pot:
                return ActionCall()

            return ActionFold() if state.can_act(ActionFold) else ActionCheck()

        # No bet to face.
        delayed = not is_first_to_act
        if strong_made or eq >= 0.72:
            if state.can_act(ActionRaise):
                frac = 0.78 if tex["very_wet"] else 0.58
                if delayed and not tex["very_wet"]:
                    frac = 0.66
                if spr <= 1.5 and eq >= 0.80:
                    return ActionRaise(state.raise_bounds[1])
                return self._pot_raise(state, frac)
            return ActionCheck()

        if medium_made or (eq >= 0.52 and not tex["very_wet"]):
            if state.can_act(ActionRaise):
                # Value/protection and delayed probes.
                if delayed:
                    freq = 0.62 if not tex["paired"] else 0.46
                else:
                    freq = 0.58 if self.last_action_was_aggro else 0.44
                if self.opp.is_passive():
                    freq += 0.06
                if self._mix(freq, state, 302):
                    frac = 0.52 if tex["very_wet"] else 0.40
                    return self._pot_raise(state, frac)
            return ActionCheck()

        if semi_bluff:
            if state.can_act(ActionRaise):
                freq = 0.34 if street == "flop" else 0.22
                if delayed:
                    freq += 0.10
                if fold_rate > 0.45:
                    freq += 0.10
                if self._mix(freq, state, 303):
                    frac = 0.58 if draws["combo_draw"] else 0.44
                    return self._pot_raise(state, frac)
            return ActionCheck()

        # Low-equity air: selective c-bets / probes only on favorable textures.
        if state.can_act(ActionRaise):
            bluff_freq = 0.0
            if delayed and not tex["very_wet"]:
                bluff_freq = 0.28
            elif not delayed and not tex["very_wet"] and self.last_action_was_aggro:
                bluff_freq = 0.20
            if self.opp.fold_to_aggression_rate() > 0.48:
                bluff_freq += 0.08
            if tex["high_card"] >= 12 and max(_rank(c) for c in state.my_hand) >= 13:
                bluff_freq += 0.05
            if self._mix(bluff_freq, state, 304):
                return self._pot_raise(state, 0.34)

        return ActionCheck()

    # def _postflop_action(self, game_info: GameInfo, state: PokerState, budget: float):
    #     street = _normalize_street(state.street)
    #     eq = self._equity(state, budget)
    #     made = made_hand_info(state.my_hand, state.board)
    #     draws = draw_info(state.my_hand, state.board)
    #     tex = board_texture(state.board)
    #     cost = state.cost_to_call
    #     pot = max(1, state.pot)
    #     spr = stack_to_pot_ratio(state)
    #     fold_rate = self.opp.fold_to_aggression_rate()
    #     is_first_to_act = state.is_bb  # BB acts first on all postflop streets in this engine.

    #     # --- FIX 1: Respect Massive Aggression ---
    #     # If facing a near-pot bet or overbet, slash our equity because the
    #     # opponent's range is heavily polarized to monsters.
    #     if cost >= pot * 0.70:
    #         eq *= 0.65

    #     strong_made = made["category"] >= 3 or made["overpair"] or (made["top_pair"] and eq >= 0.62)
    #     medium_made = made["category"] >= 2 or made["top_pair"] or made["second_pair"]
    #     strong_draw = draws["combo_draw"] or draws["nut_flush_draw"] or (draws["flush_draw"] and draws["oesd"])
    #     # semi_bluff = strong_draw or (draws["flush_draw"] and eq >= 0.30) or (draws["oesd"] and eq >= 0.28)
    #     semi_bluff = False

    #     # Facing a bet / raise.
    #     if cost > 0:
    #         need = pot_odds_threshold(cost, pot)
    #         mdf = minimum_defense_fraction(cost, pot)
    #         # Slight anti-overfold floor in frequent small-bet spots.
    #         call_floor = 0.0
    #         if cost <= 0.33 * pot:
    #             call_floor = 0.02 + 0.03 * (mdf - 0.50)

    #         if strong_made and eq >= 0.66:
    #             # --- FIX 2: Cap Raises on Paired Boards ---
    #             # Do not get into 4-bet wars with straights/flushes if the board
    #             # is paired and allows for a Full House or better (category < 6).
    #             if tex["paired"] and made["category"] < 6:
    #                 if eq + call_floor >= need:
    #                     return ActionCall()
    #                 return ActionFold() if state.can_act(ActionFold) else ActionCheck()

    #             if state.can_act(ActionRaise):
    #                 if spr <= 1.6:
    #                     return ActionRaise(state.raise_bounds[1])
    #                 frac = 0.95 if tex["very_wet"] else 0.70
    #                 return self._pot_raise(state, frac)
    #             return ActionCall()

    #         if semi_bluff and eq + 0.06 >= need:
    #             if state.can_act(ActionRaise) and fold_rate >= 0.34 and self._mix(0.26 if street == "flop" else 0.18, state, 301):
    #                 frac = 0.72 if tex["very_wet"] else 0.58
    #                 return self._pot_raise(state, frac)
    #             return ActionCall()

    #         if medium_made and eq + call_floor >= need:
    #             return ActionCall()

    #         if eq + call_floor >= need and cost <= 0.20 * pot:
    #             return ActionCall()

    #         return ActionFold() if state.can_act(ActionFold) else ActionCheck()

    #     # No bet to face.
    #     delayed = not is_first_to_act
    #     if strong_made or eq >= 0.72:
    #         if state.can_act(ActionRaise):
    #             frac = 0.78 if tex["very_wet"] else 0.58
    #             if delayed and not tex["very_wet"]:
    #                 frac = 0.66
    #             if spr <= 1.5 and eq >= 0.80:
    #                 return ActionRaise(state.raise_bounds[1])
    #             return self._pot_raise(state, frac)
    #         return ActionCheck()

    #     if medium_made or (eq >= 0.52 and not tex["very_wet"]):
    #         if state.can_act(ActionRaise):
    #             # Value/protection and delayed probes.
    #             if delayed:
    #                 freq = 0.62 if not tex["paired"] else 0.46
    #             else:
    #                 freq = 0.58 if self.last_action_was_aggro else 0.44
    #             if self.opp.is_passive():
    #                 freq += 0.06
    #             if self._mix(freq, state, 302):
    #                 frac = 0.52 if tex["very_wet"] else 0.40
    #                 return self._pot_raise(state, frac)
    #         return ActionCheck()

    #     if semi_bluff:
    #         if state.can_act(ActionRaise):
    #             freq = 0.34 if street == "flop" else 0.22
    #             if delayed:
    #                 freq += 0.10
    #             if fold_rate > 0.45:
    #                 freq += 0.10
    #             if self._mix(freq, state, 303):
    #                 frac = 0.58 if draws["combo_draw"] else 0.44
    #                 return self._pot_raise(state, frac)
    #         return ActionCheck()

    #     # Low-equity air: selective c-bets / probes only on favorable textures.
    #     if state.can_act(ActionRaise):
    #         bluff_freq = 0.0
    #         if delayed and not tex["very_wet"]:
    #             bluff_freq = 0.28
    #         elif not delayed and not tex["very_wet"] and self.last_action_was_aggro:
    #             bluff_freq = 0.20
    #         if self.opp.fold_to_aggression_rate() > 0.48:
    #             bluff_freq += 0.08
    #         if tex["high_card"] >= 12 and max(_rank(c) for c in state.my_hand) >= 13:
    #             bluff_freq += 0.05
    #         if self._mix(bluff_freq, state, 304):
    #             return self._pot_raise(state, 0.34)

    #     return ActionCheck()

    # # ------------------------------------------------------------------
    # # River
    # # ------------------------------------------------------------------
    def _river_action(self, game_info: GameInfo, state: PokerState, budget: float):
        eq = self._equity(state, budget)
        made = made_hand_info(state.my_hand, state.board)
        tex = board_texture(state.board)
        cost = state.cost_to_call
        pot = max(1, state.pot)
        fold_rate = self.opp.fold_to_aggression_rate()
        delayed = not state.is_bb
        blocker = max(_rank(c) for c in state.my_hand) >= 13

        nutted = made["category"] >= 4
        strong_showdown = nutted or eq >= 0.72 or (made["overpair"] and eq >= 0.68)
        bluff_catcher = eq >= 0.42 or made["top_pair"] or made["category"] >= 2

        if cost > 0:
            need = pot_odds_threshold(cost, pot)
            if strong_showdown:
                if state.can_act(ActionRaise) and eq >= 0.82:
                    # Polar value raise.
                    frac = 0.90 if tex["very_wet"] else 0.75
                    return self._pot_raise(state, frac)
                return ActionCall()
            if bluff_catcher and eq + 0.01 >= need:
                return ActionCall()
            if eq >= need and cost <= 0.18 * pot:
                return ActionCall()
            return ActionFold() if state.can_act(ActionFold) else ActionCheck()

        # No bet to face on river.
        if strong_showdown:
            if state.can_act(ActionRaise):
                frac = 0.86 if eq >= 0.86 else 0.62
                return self._pot_raise(state, frac)
            return ActionCheck()

        if eq >= 0.56 and state.can_act(ActionRaise):
            thin_freq = 0.28 if not tex["very_wet"] else 0.16
            if delayed:
                thin_freq += 0.08
            if self._mix(thin_freq, state, 401):
                return self._pot_raise(state, 0.42)

        # Polar bluff: use blockers and only vs folders.
        if state.can_act(ActionRaise):
            bluff_freq = 0.0
            if fold_rate >= 0.52 and not tex["paired"]:
                bluff_freq = 0.14
            if blocker:
                bluff_freq += 0.05
            if delayed:
                bluff_freq += 0.05
            if eq <= 0.28 and self._mix(bluff_freq, state, 402):
                frac = 0.72 if fold_rate >= 0.60 else 0.55
                return self._pot_raise(state, frac)

        return ActionCheck()

    # def _river_action(self, game_info: GameInfo, state: PokerState, budget: float):
    #     eq = self._equity(state, budget)
    #     made = made_hand_info(state.my_hand, state.board)
    #     tex = board_texture(state.board)
    #     cost = state.cost_to_call
    #     pot = max(1, state.pot)

    #     # --- FIX 2: Respect Massive Aggression ---
    #     # If facing a near-pot bet or a massive overbet, mathematically discount our
    #     # raw equity because the opponent's range is heavily polarized to the nuts.
    #     if cost >= pot * 0.8:
    #         eq *= 0.65

    #     fold_rate = self.opp.fold_to_aggression_rate()
    #     delayed = not state.is_bb
    #     blocker = max(_rank(c) for c in state.my_hand) >= 13

    #     nutted = made["category"] >= 4
    #     strong_showdown = nutted or eq >= 0.72 or (made["overpair"] and eq >= 0.68)
    #     bluff_catcher = eq >= 0.42 or made["top_pair"] or made["category"] >= 2

    #     if cost > 0:
    #         need = pot_odds_threshold(cost, pot)
    #         if strong_showdown:
    #             # --- FIX 1: Cap Raises on Paired Boards ---
    #             # Stop the bot from getting into 5-bet wars on the river with weak
    #             # two-pairs when the board is paired and counterfeiting is highly likely.
    #             if tex["paired"] and made["category"] < 6:
    #                 return ActionCall()

    #             if state.can_act(ActionRaise) and eq >= 0.82:
    #                 # Polar value raise.
    #                 frac = 0.90 if tex["very_wet"] else 0.75
    #                 return self._pot_raise(state, frac)
    #             return ActionCall()

    #         if bluff_catcher and eq + 0.01 >= need:
    #             return ActionCall()
    #         if eq >= need and cost <= 0.18 * pot:
    #             return ActionCall()
    #         return ActionFold() if state.can_act(ActionFold) else ActionCheck()

    #     # No bet to face on river.
    #     if strong_showdown:
    #         if state.can_act(ActionRaise):
    #             frac = 0.86 if eq >= 0.86 else 0.62
    #             return self._pot_raise(state, frac)
    #         return ActionCheck()

    #     if eq >= 0.56 and state.can_act(ActionRaise):
    #         thin_freq = 0.28 if not tex["very_wet"] else 0.16
    #         if delayed:
    #             thin_freq += 0.08
    #         if self._mix(thin_freq, state, 401):
    #             return self._pot_raise(state, 0.42)

    #     # Polar bluff: use blockers and only vs folders.
    #     if state.can_act(ActionRaise):
    #         bluff_freq = 0.0
    #         if fold_rate >= 0.52 and not tex["paired"]:
    #             bluff_freq = 0.14
    #         if blocker:
    #             bluff_freq += 0.05
    #         if delayed:
    #             bluff_freq += 0.05
    #         if eq <= 0.28 and self._mix(bluff_freq, state, 402):
    #             frac = 0.72 if fold_rate >= 0.60 else 0.55
    #             return self._pot_raise(state, frac)

    #     return ActionCheck()


if __name__ == "__main__":
    run_bot(Player(), parse_args())