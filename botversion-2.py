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
        '''
        Called when a new game starts. Called exactly once.

        Arguments:
        Nothing.

        Returns:
        Nothing.
        '''
        self.evaluator = Evaluator()
        self.preflop_win_probabilities = {
            ('A', 'A'): 0.85, ('K', 'K'): 0.82, ('Q', 'Q'): 0.80, ('J', 'J'): 0.77, ('T', 'T'): 0.75,
            ('9', '9'): 0.72, ('8', '8'): 0.69, ('7', '7'): 0.66, ('6', '6'): 0.63, ('5', '5'): 0.60,
            ('4', '4'): 0.56, ('3', '3'): 0.53, ('2', '2'): 0.50,

            ('A', 'K', 's'): 0.66, ('A', 'Q', 's'): 0.65, ('A', 'J', 's'): 0.64, ('A', 'T', 's'): 0.63,
            ('K', 'Q', 's'): 0.63, ('K', 'J', 's'): 0.62, ('Q', 'J', 's'): 0.61,

            ('A', 'K', 'o'): 0.64, ('A', 'Q', 'o'): 0.63, ('A', 'J', 'o'): 0.62, ('A', 'T', 'o'): 0.61,
            ('K', 'Q', 'o'): 0.61, ('K', 'J', 'o'): 0.60, ('Q', 'J', 'o'): 0.59,
        }

    def _calculate_equity(self, current_state: PokerState, simulations: int = 3000):
        """
        Monte Carlo equity calculator using treys.
        Returns (hero_equity, opponent_equity)
        """
        hero_cards = [
            Card.new(current_state.my_hand[0][0] + current_state.my_hand[0][1]),
            Card.new(current_state.my_hand[1][0] + current_state.my_hand[1][1]),
        ]
        board_cards = [
            Card.new(card[0] + card[1])
            for card in current_state.board
        ]
        opp_cards = []
        if len(current_state.opp_revealed_cards) != 0:
            opp_cards = [
                Card.new(current_state.opp_revealed_cards[0][0] + current_state.opp_revealed_cards[0][1]),
            ]
        wins = 0
        ties = 0
        for _ in range(simulations):
            deck = Deck()
            for c in hero_cards + board_cards + opp_cards:
                deck.cards.remove(c)

            rem_opp_cards = deck.draw(2-len(opp_cards))
            opp_cards = opp_cards + rem_opp_cards

            remaining_board = []
            if len(board_cards) < 5:
                remaining_board = deck.draw(5 - len(board_cards))

            full_board = board_cards + remaining_board

            hero_score = self.evaluator.evaluate(full_board, hero_cards)
            opp_score = self.evaluator.evaluate(full_board, opp_cards)

            # IMPORTANT: lower score = stronger hand in treys
            if hero_score < opp_score:
                wins += 1
            elif hero_score == opp_score:
                ties += 1

        hero_equity = (wins + ties / 2) / simulations
        opponent_equity = 1 - hero_equity
        return hero_equity, opponent_equity

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState) -> None:
        '''
        Called when a round ends. Called NUM_ROUNDS times.

        Arguments:
        game_info: the GameInfo object.
        current_state: the PokerState object.

        Returns:
        Nothing.
        '''
        pass

    def get_move(self, game_info: GameInfo, current_state: PokerState) -> ActionFold | ActionCall | ActionCheck | ActionRaise | ActionBid:
        '''
        Where the magic happens - your code should implement this function.
        Called any time the engine needs an action from your bot.
        '''
        street = current_state.street
        if street == 'preflop':
            hand = current_state.my_hand
            card1_rank = hand[0][0]
            card2_rank = hand[1][0]
            card1_suit = hand[0][1]
            card2_suit = hand[1][1]

            ranks = '23456789TJQKA'
            rank1_val = ranks.find(card1_rank)
            rank2_val = ranks.find(card2_rank)

            if rank1_val < rank2_val:
                card1_rank, card2_rank = card2_rank, card1_rank

            if card1_rank == card2_rank:
                hand_key = (card1_rank, card2_rank)
            elif card1_suit == card2_suit:
                hand_key = (card1_rank, card2_rank, 's')
            else:
                hand_key = (card1_rank, card2_rank, 'o')

            win_prob = self.preflop_win_probabilities.get(hand_key, 0.5)

            if win_prob > 0.6:
                if current_state.can_act(ActionRaise):
                    min_raise, max_raise = current_state.raise_bounds
                    return ActionRaise(min_raise)
                else:
                    return ActionCall()
            elif win_prob > 0.45:
                return ActionCall()
            else:
                if current_state.can_act(ActionCheck):
                    return ActionCheck()
                elif current_state.can_act(ActionCall):
                    return ActionCall()
                else:
                    return ActionFold()
        elif street == 'auction':
            return self._get_auction_move(current_state)
        else: # Flop, Turn, River
            return self._get_postflop_move(current_state)

    def _get_preflop_move(self, current_state: PokerState) -> ActionFold | ActionCall | ActionCheck | ActionRaise:
        '''
        Returns the move for the pre-flop street.
        '''

        # Starting with a very simple pre-flop strategy based on hand strength. This is not a good strategy, but it's a starting point.
        hand = current_state.my_hand
        card1_rank = hand[0][0]
        card2_rank = hand[1][0]
        card1_suit = hand[0][1]
        card2_suit = hand[1][1]

        ranks = '23456789TJQKA'
        rank1_val = ranks.find(card1_rank)
        rank2_val = ranks.find(card2_rank)

        if rank1_val < rank2_val:
            card1_rank, card2_rank = card2_rank, card1_rank

        if card1_rank == card2_rank:
            hand_key = (card1_rank, card2_rank)
        elif card1_suit == card2_suit:
            hand_key = (card1_rank, card2_rank, 's')
        else:
            hand_key = (card1_rank, card2_rank, 'o')

        win_prob = self.preflop_win_probabilities.get(hand_key, 0.5)

        if win_prob > 0.6:
            if current_state.can_act(ActionRaise):
                min_raise, max_raise = current_state.raise_bounds
                return ActionRaise(min_raise)
            else:
                return ActionCall()
        elif win_prob > 0.45:
            return ActionCall()
        else:
            if current_state.can_act(ActionCheck):
                return ActionCheck()
            elif current_state.can_act(ActionCall):
                return ActionCall()
            else:
                return ActionFold()

    def _get_auction_move(self, current_state: PokerState) -> ActionBid:
        '''
        Returns the move for the auction street.
        '''
        if current_state.my_chips > 10:
            return ActionBid(10)
        else:
            return ActionBid(0)

    def _get_postflop_move(self, current_state: PokerState) -> ActionFold | ActionCall | ActionCheck | ActionRaise:
        '''
        Returns the move for post-flop streets (flop, turn, river).
        '''
        # num_sims = 500 if current_state.street == 'flop' else 1000
        # hero_equity, opp_equity = self._calculate_equity(current_state, num_sims)

        # if hero_equity > 0.8:
        #     if current_state.can_act(ActionRaise):
        #         min_raise, max_raise = current_state.raise_bounds
        #         return ActionRaise(min_raise)
        #     return ActionCall()
        # elif hero_equity > 0.6:
        #     if current_state.can_act(ActionCall):
        #         return ActionCall()
        #     return ActionCheck()
        # elif hero_equity > 0.4:
        #     if current_state.can_act(ActionCheck):
        #         return ActionCheck()
        #     if current_state.can_act(ActionCall) and current_state.cost_to_call <= 20:
        #         return ActionCall()

        # return ActionCheck() if current_state.can_act(ActionCheck) else ActionFold()
        hand_strength = self._get_hand_strength(current_state)
        if hand_strength >= 3:  # Three of a kind or better
            if current_state.can_act(ActionRaise):
                min_raise, max_raise = current_state.raise_bounds
                return ActionRaise(min_raise)
            else:
                return ActionCall()
        elif hand_strength >= 2:  # Two pair
            if current_state.can_act(ActionCheck):
                return ActionCheck()
            elif current_state.can_act(ActionCall):
                return ActionCall()
            else:
                return ActionFold()
        elif hand_strength >= 1: # One pair
            if current_state.can_act(ActionCheck):
                return ActionCheck()
            else:
                return ActionCall()
        else: # High card
            if current_state.can_act(ActionCheck):
                return ActionCheck()
            elif current_state.can_act(ActionCall):
                return ActionCall()
            else:
                return ActionFold()

    def _get_hand_strength(self, current_state: PokerState) -> int:
        '''
        A helper function to evaluate the strength of the current hand.
        Returns an integer representing the hand strength:
        0: High card
        1: One pair
        2: Two pair
        3: Three of a kind
        4: Straight
        5: Flush
        6: Full House
        7: Four of a kind
        8: Straight flush
        '''
        my_cards = current_state.my_hand
        board_cards = current_state.board
        all_cards = my_cards + board_cards

        ranks = '23456789TJQKA'
        suits = 'shdc'

        rank_counts = {rank: 0 for rank in ranks}
        suit_counts = {suit: 0 for suit in suits}

        for card in all_cards:
            rank_counts[card[0]] += 1
            suit_counts[card[1]] += 1

        # --- Check for hands ---

        # Flushes
        is_flush = False
        flush_suit = None
        for suit in suit_counts:
            if suit_counts[suit] >= 5:
                is_flush = True
                flush_suit = suit
                break

        # Straights
        is_straight = False
        straight_cards = []
        for i in range(len(ranks) - 4):
            if all(rank_counts[ranks[j]] > 0 for j in range(i, i+5)):
                is_straight = True

        # Straight Flush
        if is_flush and is_straight:
            flush_cards = [card for card in all_cards if card[1] == flush_suit]
            flush_ranks = ''.join(sorted([card[0] for card in flush_cards], key=lambda r: ranks.find(r)))
            for i in range(len(ranks) - 4):
                 if ranks[i:i+5] in flush_ranks:
                     return 8

        # Four of a kind
        if 4 in rank_counts.values():
            return 7

        # Full House
        if 3 in rank_counts.values() and 2 in rank_counts.values():
            return 6

        # Flush
        if is_flush:
            return 5

        # Straight
        if is_straight:
            return 4

        # Three of a kind
        if 3 in rank_counts.values():
            return 3

        # Two pair
        pairs = 0
        for rank in rank_counts:
            if rank_counts[rank] == 2:
                pairs += 1
        if pairs >= 2:
            return 2

        # One pair
        if 2 in rank_counts.values():
            return 1

        return 0
if __name__ == '__main__':
    run_bot(Player(), parse_args())
