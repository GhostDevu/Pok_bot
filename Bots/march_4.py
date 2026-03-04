from __future__ import annotations

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
    import eval7
except Exception:
    eval7 = None

try:
    from treys import Card as TreysCard, Evaluator as TreysEvaluator
    _TREYS_EVALUATOR = TreysEvaluator()
    _treys_available = True
except Exception:
    _treys_available = False
    _TREYS_EVALUATOR = None

STARTING_STACK = 5000
SMALL_BLIND = 10
BIG_BLIND = 20
NUM_ROUNDS = 1000
SOFT_TOTAL_TIME = 19.5
PER_ACTION_HARD_CAP = 1.80

RANKS = "23456789TJQKA"
SUITS = "cdhs"
RANK_TO_INT = {r: i + 2 for i, r in enumerate(RANKS)}
FULL_DECK = [r + s for r in RANKS for s in SUITS]


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
    return "pre-flop" if street in ("preflop", "pre-flop") else street


def _effective_stack(state: PokerState) -> int:
    return min(state.my_chips, state.opp_chips)


def _remaining_cards(excluded: set[str]) -> list[str]:
    return [c for c in FULL_DECK if c not in excluded]


def _straight_high(ranks_desc: list[int]) -> int:
    uniq = sorted(set(ranks_desc), reverse=True)
    if 14 in uniq:
        uniq.append(1)
    run, best = 1, 0
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
    best = None
    for combo in combinations(cards_key, 5):
        val = _eval_five(combo)
        if best is None or val > best:
            best = val
    return best


@lru_cache(maxsize=200000)
def _best_rank_eval7(cards_key: tuple[str, ...]):
    return eval7.evaluate([_card_obj(c) for c in cards_key])


@lru_cache(maxsize=200000)
def _best_rank_treys(cards_key: tuple[str, ...]) -> int:
    treys_cards = [TreysCard.new(c) for c in cards_key]
    n = len(treys_cards)
    if n == 5:
        return _TREYS_EVALUATOR.evaluate([], treys_cards)
    best = None
    for combo in combinations(range(n), 5):
        score = _TREYS_EVALUATOR.evaluate([], [treys_cards[i] for i in combo])
        if best is None or score < best:
            best = score
    return best if best is not None else 9999


def _best_rank(cards: list[str]):
    key = tuple(sorted(cards))
    if eval7 is not None:
        return _best_rank_eval7(key)
    if _treys_available:
        return _best_rank_treys(key)
    return _best_rank_fallback(key)


def _compare_hands(cards_a: list[str], cards_b: list[str]) -> int:
    va, vb = _best_rank(cards_a), _best_rank(cards_b)
    if eval7 is None and _treys_available:
        return (va < vb) - (va > vb)
    return (va > vb) - (va < vb)


def preflop_strength(hand: list[str]) -> float:
    if len(hand) != 2:
        return 0.5
    r1, r2 = _rank(hand[0]), _rank(hand[1])
    hi, lo = max(r1, r2), min(r1, r2)
    suited = _suit(hand[0]) == _suit(hand[1])
    gap = hi - lo
    pair = hi == lo
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
    return max(0.02, min(0.99, (base - 2.0) / 33.0))


def board_texture(board: list[str]) -> dict:
    if not board:
        return {"paired": False, "monotone": False, "two_tone": False,
                "connected": False, "very_wet": False, "high_card": 0}
    ranks = sorted((_rank(c) for c in board), reverse=True)
    uniq = sorted(set(ranks))
    suits = [_suit(c) for c in board]
    suit_counts = {s: suits.count(s) for s in set(suits)}
    max_suit = max(suit_counts.values())
    gaps = [uniq[i + 1] - uniq[i] for i in range(len(uniq) - 1)]
    connected = bool(gaps) and min(gaps) <= 2 and (max(uniq) - min(uniq) <= 5)
    paired = len(uniq) < len(ranks)
    monotone = max_suit >= 3
    two_tone = max_suit == 2
    very_wet = monotone or (connected and two_tone) or (paired and two_tone)
    return {"paired": paired, "monotone": monotone, "two_tone": two_tone,
            "connected": connected, "very_wet": very_wet,
            "broadways": sum(1 for r in ranks if r >= 11), "high_card": max(ranks)}


def made_hand_info(hand: list[str], board: list[str]) -> dict:
    cards = hand + board
    ranks = [_rank(c) for c in cards]
    suits = [_suit(c) for c in cards]
    rank_counts = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1
    counts_sorted = sorted(rank_counts.values(), reverse=True)
    flush = any(suits.count(s) >= 5 for s in set(suits))
    flush_suit = next((s for s in set(suits) if suits.count(s) >= 5), None)
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
    top_pair = second_pair = overpair = False
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
    return {"category": category, "top_pair": top_pair, "second_pair": second_pair,
            "overpair": overpair, "pocket_pair": pocket_pair,
            "flush": flush, "flush_suit": flush_suit, "straight": straight}


def draw_info(hand: list[str], board: list[str]) -> dict:
    cards = hand + board
    suits = [_suit(c) for c in cards]
    ranks = sorted(set(_rank(c) for c in cards))
    flush_draw = nut_flush_draw = False
    for s in set(suits):
        if suits.count(s) == 4 and len(board) < 5:
            flush_draw = True
            suited_hole = [c for c in hand if _suit(c) == s]
            if suited_hole and max(_rank(c) for c in suited_hole) == 14:
                nut_flush_draw = True
    ext = set(ranks)
    if 14 in ext:
        ext.add(1)
    oesd = gutshot = False
    for start in range(1, 11):
        window = {start + i for i in range(5)}
        hits = len(window & ext)
        if hits >= 4:
            missing = list(window - ext)
            if len(missing) == 1:
                if missing[0] in (start, start + 4):
                    oesd = True
                else:
                    gutshot = True
    return {"flush_draw": flush_draw, "nut_flush_draw": nut_flush_draw,
            "oesd": oesd, "gutshot": gutshot, "combo_draw": flush_draw and (oesd or gutshot)}


def pot_odds_threshold(cost_to_call: int, pot: int) -> float:
    if cost_to_call <= 0:
        return 0.0
    denom = pot + cost_to_call
    return cost_to_call / denom if denom > 0 else 1.0


def minimum_defense_fraction(cost_to_call: int, pot: int) -> float:
    if cost_to_call <= 0:
        return 1.0
    return pot / (pot + cost_to_call) if (pot + cost_to_call) > 0 else 0.0


def stack_to_pot_ratio(state: PokerState) -> float:
    return _effective_stack(state) / max(1, state.pot)


class EquityEngine:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.cache: dict[tuple, float] = {}

    def clear_hand_cache(self) -> None:
        self.cache.clear()

    def equity(self, hand, board, opp_known, approx_budget, villain_tightness) -> float:
        key = (tuple(sorted(hand)), tuple(board), tuple(sorted(opp_known)),
               len(board), round(villain_tightness, 2))
        if key in self.cache:
            return self.cache[key]
        val = (self._river_equity if len(board) == 5 else self._sample_equity)(
            hand, board, opp_known, approx_budget, villain_tightness)
        self.cache[key] = val
        return val

    def _river_equity(self, hand, board, opp_known, approx_budget, villain_tightness) -> float:
        known = set(hand) | set(board) | set(opp_known)
        rem = _remaining_cards(known)
        my_cards = hand + board
        wins = total = 0.0
        if len(opp_known) == 1:
            for c in rem:
                opp = opp_known + [c]
                w = self._combo_weight(opp, villain_tightness)
                total += w
                cmpv = _compare_hands(my_cards, opp + board)
                wins += w if cmpv > 0 else (0.5 * w if cmpv == 0 else 0)
        elif len(rem) <= 48 and approx_budget >= 0.002:
            for i in range(len(rem)):
                for j in range(i + 1, len(rem)):
                    opp = [rem[i], rem[j]]
                    w = self._combo_weight(opp, villain_tightness)
                    total += w
                    cmpv = _compare_hands(my_cards, opp + board)
                    wins += w if cmpv > 0 else (0.5 * w if cmpv == 0 else 0)
        else:
            for _ in range(max(24, min(80, int(approx_budget * 4000)))):
                opp = self.rng.sample(rem, 2)
                w = self._combo_weight(opp, villain_tightness)
                total += w
                cmpv = _compare_hands(my_cards, opp + board)
                wins += w if cmpv > 0 else (0.5 * w if cmpv == 0 else 0)
        return wins / total if total > 0 else 0.5

    def _sample_equity(self, hand, board, opp_known, approx_budget, villain_tightness) -> float:
        known = set(hand) | set(board) | set(opp_known)
        rem = _remaining_cards(known)
        board_to_come = 5 - len(board)
        opp_unknown = 2 - len(opp_known)
        sp = 1.8 if (_treys_available and eval7 is None) else 1.0
        n = len(board)
        if n == 0:
            samples = max(0, min(int(40 * sp), int(approx_budget * 1800 * sp)))
        elif n == 3:
            samples = max(18, min(int(60 * sp), int(approx_budget * 2200 * sp)))
        elif n == 4:
            samples = max(18, min(int(72 * sp), int(approx_budget * 2600 * sp)))
        else:
            samples = max(16, min(int(40 * sp), int(approx_budget * 1800 * sp)))
        if samples <= 0:
            return preflop_strength(hand)
        wins = total = 0.0
        for _ in range(samples):
            draw = self.rng.sample(rem, board_to_come + opp_unknown)
            runout = board + draw[:board_to_come]
            opp = opp_known + draw[board_to_come:]
            w = self._combo_weight(opp, villain_tightness)
            total += w
            cmpv = _compare_hands(hand + runout, opp + runout)
            wins += w if cmpv > 0 else (0.5 * w if cmpv == 0 else 0)
        return wins / total if total > 0 else 0.5

    @staticmethod
    def _combo_weight(opp_cards: list[str], villain_tightness: float) -> float:
        if len(opp_cards) != 2:
            return 1.0
        s = preflop_strength(opp_cards)
        bias = 0.55 + 0.90 * villain_tightness
        return max(0.30, 1.0 + bias * (s - 0.50))


class OpponentModel:
    def __init__(self):
        self.hands = 0
        self.vpip = self.preflop_raise = self.aggressive_actions = 0.0
        self.calls = self.fold_to_our_aggression = self.faced_our_aggression = 0.0
        self.showdowns = self.showdown_wins = 0.0
        self.exact_auction_bids: list[int] = []
        self.auction_lower_bounds: list[int] = []
        self.recent_payoffs: list[int] = []

    def decay(self) -> None:
        for attr in ("vpip", "preflop_raise", "aggressive_actions", "calls",
                     "fold_to_our_aggression", "faced_our_aggression", "showdowns", "showdown_wins"):
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

    def record_vpip(self): self.vpip += 1.0
    def record_preflop_raise(self): self.preflop_raise += 1.0; self.aggressive_actions += 1.0
    def record_aggressive(self): self.aggressive_actions += 1.0
    def record_call(self): self.calls += 1.0
    def record_fold_to_our_aggression(self): self.fold_to_our_aggression += 1.0; self.faced_our_aggression += 1.0
    def record_survived_our_aggression(self): self.faced_our_aggression += 1.0
    def record_showdown(self, opp_won: bool): self.showdowns += 1.0; self.showdown_wins += (1.0 if opp_won else 0.0)
    def record_auction_exact(self, bid: int):
        if bid >= 0: self.exact_auction_bids.append(int(bid))
    def record_auction_lower_bound(self, bid_lb: int):
        if bid_lb > 0: self.auction_lower_bounds.append(int(bid_lb))
    def record_payoff(self, delta: int): self.recent_payoffs.append(delta)

    def vpip_rate(self) -> float: return self.vpip / max(1.0, min(self.hands, 120.0))
    def pfr_rate(self) -> float: return self.preflop_raise / max(1.0, min(self.hands, 120.0))
    def aggression_factor(self) -> float:
        return min(6.0, self.aggressive_actions / self.calls) if self.calls > 0.2 else (2.5 if self.aggressive_actions > 0 else 1.0)
    def fold_to_aggression_rate(self) -> float:
        return max(0.08, min(0.85, self.fold_to_our_aggression / self.faced_our_aggression)) if self.faced_our_aggression >= 1.0 else 0.38
    def showdown_win_rate(self) -> float:
        return self.showdown_wins / self.showdowns if self.showdowns >= 1.0 else 0.50
    def villain_tightness(self) -> float:
        return max(0.0, min(1.0, 0.60 - 0.90 * self.vpip_rate() + 0.35 * self.pfr_rate()))
    def estimated_auction_bid(self) -> float:
        exact_mean = (sum(self.exact_auction_bids[-40:]) / min(40, len(self.exact_auction_bids))) if self.exact_auction_bids else 140.0
        if self.auction_lower_bounds:
            lb = sum(self.auction_lower_bounds[-20:]) / min(20, len(self.auction_lower_bounds))
            return max(exact_mean, 0.85 * lb)
        return exact_mean
    def is_loose(self) -> bool: return self.vpip_rate() > 0.60
    def is_tight(self) -> bool: return self.vpip_rate() < 0.38
    def is_aggressive(self) -> bool: return self.aggression_factor() > 1.8
    def is_passive(self) -> bool: return self.aggression_factor() < 0.9


class Player(BaseBot):
    def __init__(self):
        self.rng = random.Random(20260301)
        self.eq = EquityEngine(self.rng)
        self.opp = OpponentModel()
        self.internal_time_used = 0.0
        self.last_seen_state: PokerState | None = None
        self.last_action_was_aggro = False
        self.last_aggressive_street: str | None = None
        self.preflop_opened = False
        self.preflop_raised_once = False
        self.hand_id = 0
        self.opp_vpip_this_hand = False
        self.opp_pfr_this_hand = False
        self.awaiting_auction_resolution = False
        self.last_bid_amount = 0
        self.pre_auction_my_chips = STARTING_STACK
        self.pre_auction_opp_chips = STARTING_STACK

    def on_hand_start(self, game_info: GameInfo, current_state: PokerState) -> None:
        self.hand_id += 1
        self.eq.clear_hand_cache()
        self.opp.on_hand_start()
        self.last_seen_state = current_state
        self.last_action_was_aggro = False
        self.last_aggressive_street = None
        self.preflop_opened = self.preflop_raised_once = False
        self.awaiting_auction_resolution = False
        self.last_bid_amount = 0
        self.opp_vpip_this_hand = self.opp_pfr_this_hand = False
        self.pre_auction_my_chips = current_state.my_chips
        self.pre_auction_opp_chips = current_state.opp_chips

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState) -> None:
        self._observe_terminal(current_state)
        self.opp.record_payoff(current_state.payoff)
        self.last_seen_state = None
        self.last_action_was_aggro = False
        self.last_aggressive_street = None
        self.awaiting_auction_resolution = False

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
        self._record_our_action(current_state, move)
        self.last_seen_state = current_state
        self.internal_time_used += time.perf_counter() - t0
        return move

    def _observe_transition(self, state: PokerState) -> None:
        prev = self.last_seen_state
        if prev is None:
            self.last_seen_state = state
            return
        if self.awaiting_auction_resolution and _normalize_street(state.street) != "auction":
            self._resolve_auction_from_state(state)
            self.awaiting_auction_resolution = False
        prev_street = _normalize_street(prev.street)
        cur_street = _normalize_street(state.street)
        if state.opp_wager > prev.opp_wager:
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
        if prev_street != cur_street:
            if self.last_action_was_aggro and self.last_aggressive_street == prev_street:
                self.opp.record_survived_our_aggression()
            if prev_street == "pre-flop" and prev.opp_wager > BIG_BLIND and not self.opp_vpip_this_hand:
                self.opp.record_vpip()
                self.opp_vpip_this_hand = True
        self.last_seen_state = state

    def _observe_terminal(self, state: PokerState) -> None:
        if len(state.opp_revealed_cards) >= 2:
            if self.last_action_was_aggro:
                self.opp.record_survived_our_aggression()
            self.opp.record_showdown(opp_won=state.payoff < 0)
        elif self.last_action_was_aggro and state.payoff > 0:
            self.opp.record_fold_to_our_aggression()

    def _resolve_auction_from_state(self, state: PokerState) -> None:
        my_paid = max(0, self.pre_auction_my_chips - state.my_chips)
        opp_paid = max(0, self.pre_auction_opp_chips - state.opp_chips)
        reveals = len(state.opp_revealed_cards)
        if reveals == 1 and my_paid > 0 and opp_paid > 0 and my_paid == opp_paid:
            self.opp.record_auction_exact(opp_paid)
        elif reveals == 1 and my_paid > 0 and opp_paid == 0:
            self.opp.record_auction_exact(my_paid)
        elif reveals == 0 and opp_paid > 0:
            self.opp.record_auction_lower_bound(self.last_bid_amount + 1)
        elif reveals == 1 and my_paid == 0 and opp_paid == 0:
            self.opp.record_auction_exact(0)

    def _record_our_action(self, state: PokerState, action) -> None:
        street = _normalize_street(state.street)
        if isinstance(action, ActionRaise):
            self.last_action_was_aggro = True
            self.last_aggressive_street = street
            if street == "pre-flop":
                self.preflop_opened = True
                self.preflop_raised_once = True
        elif isinstance(action, ActionBid):
            self.awaiting_auction_resolution = True
            self.last_bid_amount = action.amount
            self.pre_auction_my_chips = state.my_chips
            self.pre_auction_opp_chips = state.opp_chips
            self.last_action_was_aggro = False
        elif isinstance(action, ActionCall):
            self.last_action_was_aggro = False

    def _decision_budget(self, game_info: GameInfo) -> float:
        rounds_left = max(1, NUM_ROUNDS - game_info.round_num + 1)
        soft_remaining = min(game_info.time_bank, max(0.2, SOFT_TOTAL_TIME - self.internal_time_used))
        est_queries_left = max(40.0, rounds_left * 2.2)
        return max(0.004, min(0.060, min(soft_remaining / est_queries_left, PER_ACTION_HARD_CAP)))

    def _mix(self, probability: float, state: PokerState, salt: int) -> bool:
        if probability <= 0.0: return False
        if probability >= 1.0: return True
        h = 2166136261
        blob = f"{self.hand_id}|{state.street}|{''.join(sorted(state.my_hand))}|{''.join(state.board)}|{state.my_wager}|{state.opp_wager}|{salt}"
        for ch in blob:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        return (h / 4294967296.0) < probability

    def _equity(self, state: PokerState, budget: float) -> float:
        if _normalize_street(state.street) == "pre-flop":
            if (_treys_available or eval7 is not None) and budget >= 0.012:
                mc_eq = self.eq.equity(state.my_hand, [], state.opp_revealed_cards,
                                       budget * 0.40, self.opp.villain_tightness())
                heuristic = preflop_strength(state.my_hand)
                mc_weight = min(0.65, budget * 12.0)
                return mc_weight * mc_eq + (1.0 - mc_weight) * heuristic
            return preflop_strength(state.my_hand)
        return self.eq.equity(state.my_hand, state.board, state.opp_revealed_cards,
                              budget, self.opp.villain_tightness())

    def _raise_to(self, state: PokerState, target: int) -> ActionRaise:
        mn, mx = state.raise_bounds
        return ActionRaise(max(mn, min(mx, int(target))))

    def _pot_raise(self, state: PokerState, fraction: float) -> ActionRaise:
        return self._raise_to(state, state.my_wager + int(max(1, state.pot) * fraction))

    def _safe_action(self, state: PokerState):
        if state.can_act(ActionCheck): return ActionCheck()
        if state.can_act(ActionCall):
            if state.cost_to_call <= max(BIG_BLIND, int(0.12 * max(1, state.pot))):
                return ActionCall()
            return ActionFold()
        if state.can_act(ActionBid): return ActionBid(0)
        return ActionFold()

    def _is_legal(self, action, state: PokerState) -> bool:
        if isinstance(action, ActionFold): return state.can_act(ActionFold)
        if isinstance(action, ActionCheck): return state.can_act(ActionCheck)
        if isinstance(action, ActionCall): return state.can_act(ActionCall)
        if isinstance(action, ActionRaise):
            if not state.can_act(ActionRaise): return False
            mn, mx = state.raise_bounds
            return mn <= action.amount <= mx
        if isinstance(action, ActionBid):
            return state.can_act(ActionBid) and 0 <= action.amount <= state.my_chips
        return False

    def _auction_action(self, game_info: GameInfo, state: PokerState, budget: float):
        eq = self._equity(state, budget)
        pot = max(1, state.pot)
        eff = _effective_stack(state)
        uncertainty = 4.0 * eq * (1.0 - eq)
        raw_value = uncertainty * (0.085 * pot + 0.018 * eff)
        opp_est = self.opp.estimated_auction_bid()
        if self.opp.is_aggressive(): raw_value *= 1.08
        if self.opp.is_passive(): raw_value *= 0.95
        if eq <= 0.18 or eq >= 0.82: raw_value *= 0.38
        bid = int(max(0.0, raw_value))
        if 0.45 <= eq <= 0.62 and bid < opp_est * 0.85:
            bid = int(0.85 * opp_est)
        if 0.38 <= eq <= 0.68 and self._mix(0.22, state, 901):
            bid = int(max(bid, opp_est + BIG_BLIND))
        cap = min(state.my_chips, max(0, int(0.14 * STARTING_STACK + 0.03 * pot)))
        bid = max(0, min(cap, bid))
        if opp_est > 260 and uncertainty < 0.55 and bid < opp_est * 0.55:
            bid = 0
        return ActionBid(bid)

    def _preflop_action(self, game_info: GameInfo, state: PokerState, budget: float):
        s = preflop_strength(state.my_hand)
        cost = state.cost_to_call
        pot = max(1, state.pot)
        is_bb = state.is_bb
        blocker = max(_rank(c) for c in state.my_hand) >= 13

        if not is_bb:
            if state.my_wager == SMALL_BLIND and cost > 0:
                if not state.can_act(ActionRaise):
                    return ActionCall() if (s >= 0.46 or (s >= 0.34 and cost <= BIG_BLIND)) else ActionFold()
                open_thresh = 0.30 - (0.04 if self.opp.is_tight() else 0) + (0.03 if (self.opp.is_loose() and self.opp.is_aggressive()) else 0)
                limp_thresh = 0.16 + (0.03 if (self.opp.is_loose() and self.opp.is_aggressive()) else 0)
                if s >= 0.83:
                    return self._raise_to(state, 90 if self._mix(0.55, state, 101) else 70)
                if s >= open_thresh:
                    target = 80 if s >= 0.64 else (40 if s < 0.40 and self._mix(0.35, state, 102) else 60)
                    if self._mix(0.82 if s >= 0.45 else 0.58, state, 103):
                        return self._raise_to(state, target)
                    return ActionCall()
                if s >= limp_thresh:
                    return ActionCall() if self._mix(0.78, state, 104) else ActionFold()
                if blocker and self.opp.fold_to_aggression_rate() > 0.46 and self._mix(0.10, state, 105):
                    return self._raise_to(state, 40)
                return ActionFold()
            thresh = pot_odds_threshold(cost, pot)
            if s >= 0.88 and state.can_act(ActionRaise):
                jam_target = state.raise_bounds[1]
                if stack_to_pot_ratio(state) <= 2.0:
                    return ActionRaise(jam_target)
                return self._raise_to(state, int(state.my_wager + max(2 * cost, 0.85 * pot)))
            if s >= max(0.42, thresh + 0.06): return ActionCall()
            if blocker and s >= thresh and self._mix(0.08, state, 106): return ActionCall()
            return ActionFold()

        if cost == 0:
            if not state.can_act(ActionRaise): return ActionCheck()
            stab_freq = 1.0 if s >= 0.74 else (0.72 if s >= 0.54 else (0.34 if s >= 0.40 else (0.14 if blocker and self.opp.is_tight() else 0.0)))
            if self._mix(stab_freq, state, 201):
                return self._raise_to(state, 80 if s >= 0.70 else 60)
            return ActionCheck()

        thresh = pot_odds_threshold(cost, pot)
        if s >= 0.86 and state.can_act(ActionRaise):
            target = min(state.raise_bounds[1], max(state.raise_bounds[0], int(state.my_wager + max(2.3 * cost, 0.70 * pot))))
            if stack_to_pot_ratio(state) <= 1.8:
                target = state.raise_bounds[1]
            return ActionRaise(target)
        if s >= max(0.34, thresh + 0.03):
            if s >= 0.60 and state.can_act(ActionRaise) and self._mix(0.12 if self.opp.is_aggressive() else 0.07, state, 202):
                target = min(state.raise_bounds[1], max(state.raise_bounds[0], int(state.my_wager + max(2.0 * cost, 0.60 * pot))))
                return ActionRaise(target)
            return ActionCall()
        if blocker and s >= max(0.27, thresh - 0.01) and state.can_act(ActionRaise) and self.opp.fold_to_aggression_rate() > 0.48 and self._mix(0.08, state, 203):
            target = min(state.raise_bounds[1], max(state.raise_bounds[0], int(state.my_wager + max(2.1 * cost, 0.60 * pot))))
            return ActionRaise(target)
        return ActionFold()

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
        is_first_to_act = state.is_bb

        strong_made = made["category"] >= 3 or made["overpair"] or (made["top_pair"] and eq >= 0.62)
        medium_made = made["category"] >= 2 or made["top_pair"] or made["second_pair"]

        if cost > 0:
            need = pot_odds_threshold(cost, pot)
            mdf = minimum_defense_fraction(cost, pot)
            call_floor = (0.02 + 0.03 * (mdf - 0.50)) if cost <= 0.33 * pot else 0.0
            if strong_made and eq >= 0.66:
                if state.can_act(ActionRaise):
                    if spr <= 1.6: return ActionRaise(state.raise_bounds[1])
                    return self._pot_raise(state, 0.95 if tex["very_wet"] else 0.70)
                return ActionCall()
            if medium_made and eq + call_floor >= need: return ActionCall()
            if eq + call_floor >= need and cost <= 0.20 * pot: return ActionCall()
            return ActionFold() if state.can_act(ActionFold) else ActionCheck()

        delayed = not is_first_to_act
        if strong_made or eq >= 0.72:
            if state.can_act(ActionRaise):
                frac = 0.78 if tex["very_wet"] else (0.66 if delayed else 0.58)
                if spr <= 1.5 and eq >= 0.80: return ActionRaise(state.raise_bounds[1])
                return self._pot_raise(state, frac)
            return ActionCheck()
        if medium_made or (eq >= 0.52 and not tex["very_wet"]):
            if state.can_act(ActionRaise):
                freq = (0.62 if not tex["paired"] else 0.46) if delayed else (0.58 if self.last_action_was_aggro else 0.44)
                if self.opp.is_passive(): freq += 0.06
                if self._mix(freq, state, 302):
                    return self._pot_raise(state, 0.52 if tex["very_wet"] else 0.40)
            return ActionCheck()

        if state.can_act(ActionRaise):
            bluff_freq = 0.0
            if delayed and not tex["very_wet"]: bluff_freq = 0.28
            elif not delayed and not tex["very_wet"] and self.last_action_was_aggro: bluff_freq = 0.20
            if self.opp.fold_to_aggression_rate() > 0.48: bluff_freq += 0.08
            if tex["high_card"] >= 12 and max(_rank(c) for c in state.my_hand) >= 13: bluff_freq += 0.05
            if self._mix(bluff_freq, state, 304): return self._pot_raise(state, 0.34)
        return ActionCheck()

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
                    return self._pot_raise(state, 0.90 if tex["very_wet"] else 0.75)
                return ActionCall()
            if bluff_catcher and eq + 0.01 >= need: return ActionCall()
            if eq >= need and cost <= 0.18 * pot: return ActionCall()
            return ActionFold() if state.can_act(ActionFold) else ActionCheck()

        if strong_showdown:
            if state.can_act(ActionRaise):
                return self._pot_raise(state, 0.86 if eq >= 0.86 else 0.62)
            return ActionCheck()
        if eq >= 0.56 and state.can_act(ActionRaise):
            thin_freq = (0.28 if not tex["very_wet"] else 0.16) + (0.08 if delayed else 0.0)
            if self._mix(thin_freq, state, 401): return self._pot_raise(state, 0.42)
        if state.can_act(ActionRaise):
            bluff_freq = (0.14 if fold_rate >= 0.52 and not tex["paired"] else 0.0)
            if blocker: bluff_freq += 0.05
            if delayed: bluff_freq += 0.05
            if eq <= 0.28 and self._mix(bluff_freq, state, 402):
                return self._pot_raise(state, 0.72 if fold_rate >= 0.60 else 0.55)
        return ActionCheck()


if __name__ == "__main__":
    run_bot(Player(), parse_args())