'''
Simple example pokerbot, written in Python.
'''
from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot
from treys import Card, Deck, Evaluator
import random


class Player(BaseBot):
    '''
    A pokerbot.
    '''

    def __init__(self) -> None:
        self.evaluator = Evaluator()

    def on_hand_start(self, game_info: GameInfo, current_state: PokerState) -> None:
        pass

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState) -> None:
        pass

    def get_move(self, game_info: GameInfo, current_state: PokerState) -> ActionFold | ActionCall | ActionCheck | ActionRaise | ActionBid:
        if current_state.street == 'auction':
            return self._auction_strategy(current_state)  # BUG FIX: was missing return
        elif current_state.street == 'preflop':
            return self._equity_strategy(current_state, simulations=500)  # fewer sims preflop is fine
        else:
            return self._equity_strategy(current_state, simulations=1000)

    def _auction_strategy(self, current_state: PokerState) -> ActionBid:
        '''Bid based on preflop equity.'''
        equity, _ = self._calculate_equity(current_state, simulations=200)
        # Bid proportionally to edge above 50%, capped by available chips
        edge = max(0, equity - 0.5)
        bid = int(edge * 2 * current_state.my_chips)  # scales 0 to my_chips
        bid = min(bid, current_state.my_chips)
        return ActionBid(bid)

    def _equity_strategy(self, current_state: PokerState, simulations: int = 1000) -> ActionFold | ActionCall | ActionCheck | ActionRaise:
        '''
        Unified strategy for preflop and postflop, driven entirely by equity.

        Thresholds:
          equity >= 0.65 → raise
          equity >= 0.50 → call/check
          equity <  0.50 → check if free, else fold
        '''
        equity, _ = self._calculate_equity(current_state, simulations=simulations)

        if equity >=0.85:
            # If we have a huge edge, go all-in
            if current_state.can_act(ActionRaise):
                return ActionRaise(current_state.my_chips)
            elif current_state.can_act(ActionCall):
                return ActionCall()
            else:
                return ActionCheck()

        if equity >= 0.65:
            if current_state.can_act(ActionRaise):
                min_raise, max_raise = current_state.raise_bounds
                # Size the raise proportional to equity edge
                edge = equity - 0.5
                raise_amount = int(min_raise + edge * (max_raise - min_raise))
                raise_amount = max(min_raise, min(raise_amount, max_raise))
                return ActionRaise(raise_amount)
            elif current_state.can_act(ActionCall):
                return ActionCall()
            else:
                return ActionCheck()

        elif equity >= 0.50:
            if current_state.can_act(ActionCheck):
                return ActionCheck()
            elif current_state.can_act(ActionCall):
                return ActionCall()
            else:
                return ActionFold()

        else:  # equity < 0.50 — we're behind
            if current_state.can_act(ActionCheck):
                return ActionCheck()  # free to see next card
            else:
                return ActionFold()

    def _calculate_equity(self, current_state: PokerState, simulations: int = 1000):
        """
        Monte Carlo equity calculator using treys.
        Returns (hero_equity, opponent_equity).
        """
        hero_cards = [
            Card.new(current_state.my_hand[0][0] + current_state.my_hand[0][1]),
            Card.new(current_state.my_hand[1][0] + current_state.my_hand[1][1]),
        ]
        board_cards = [
            Card.new(card[0] + card[1])
            for card in current_state.board
        ]

        # BUG FIX: capture known opp cards once, outside the loop
        known_opp_cards = []
        if len(current_state.opp_revealed_cards) != 0:
            known_opp_cards = [
                Card.new(c[0] + c[1])
                for c in current_state.opp_revealed_cards
            ]

        wins = 0
        ties = 0

        for _ in range(simulations):
            deck = Deck()

            # Remove known cards from deck
            for c in hero_cards + board_cards + known_opp_cards:
                deck.cards.remove(c)

            # BUG FIX: build opp_cards fresh each iteration so known cards
            # don't accumulate across simulations
            sim_opp_cards = known_opp_cards + deck.draw(2 - len(known_opp_cards))

            remaining_board = []
            if len(board_cards) < 5:
                remaining_board = deck.draw(5 - len(board_cards))

            full_board = board_cards + remaining_board

            hero_score = self.evaluator.evaluate(full_board, hero_cards)
            opp_score = self.evaluator.evaluate(full_board, sim_opp_cards)

            # Lower score = stronger hand in treys
            if hero_score < opp_score:
                wins += 1
            elif hero_score == opp_score:
                ties += 1

        hero_equity = (wins + ties / 2) / simulations
        return hero_equity, 1 - hero_equity


if __name__ == '__main__':
    run_bot(Player(), parse_args())