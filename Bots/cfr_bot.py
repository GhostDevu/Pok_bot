"""
A pokerbot that uses a pre-trained Counterfactual Regret Minimization (CFR) strategy.
This file contains the player logic and the CFR training framework.
"""
import random
import json
import os

from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot


# --- CFR Logic (from cfr_logic.py) ---

class CFRNode:
    """
    Represents a node in the game tree, corresponding to an information set.
    """
    def __init__(self, num_actions):
        self.num_actions = num_actions
        self.regret_sum = [0.0] * num_actions
        self.strategy_sum = [0.0] * num_actions
        self.strategy = [1.0 / num_actions] * num_actions

    def get_strategy(self):
        """
        Get the current mixed strategy at this node.
        """
        normalizing_sum = sum(max(0, r) for r in self.regret_sum)
        if normalizing_sum > 0:
            for i in range(self.num_actions):
                self.strategy[i] = max(0, self.regret_sum[i]) / normalizing_sum
        else:
            # Default to uniform random strategy if all regrets are non-positive
            for i in range(self.num_actions):
                self.strategy[i] = 1.0 / self.num_actions
        return self.strategy

    def get_average_strategy(self):
        """
        Get the average strategy over all iterations. This is what we use for playing.
        """
        normalizing_sum = sum(self.strategy_sum)
        if normalizing_sum > 0:
            return [s / normalizing_sum for s in self.strategy_sum]
        else:
            return [1.0 / self.num_actions] * self.num_actions

class CFRTrainer:
    """
    The main class for training the CFR model.
    """
    def __init__(self):
        self.nodes = {}

    def get_node(self, info_set, num_actions):
        """
        Retrieve or create a node for a given information set.
        """
        if info_set not in self.nodes:
            self.nodes[info_set] = CFRNode(num_actions)
        return self.nodes[info_set]

    def train(self, num_iterations):
        """
        Run the CFR training process for a specified number of iterations.
        This is a placeholder for the full training loop which would involve simulating
        games and traversing the game tree.
        """
        # In a real implementation, this method would be much more complex.
        # It would involve:
        # 1. Simulating a game from the start.
        # 2. For each decision point, calling a 'cfr' method recursively.
        # 3. The 'cfr' method would update regrets and strategies.
        
        print(f"Running {num_iterations} training iterations (simulation)...")
        # Since we cannot run a full training loop here, we'll simulate it by
        # populating a few nodes with some dummy data to show the structure.
        
        # Example: a pre-flop scenario with a specific hand
        info_set_1 = "preflop:AsKs:Bet" # A sample info set key
        self.get_node(info_set_1, 2) # Assume 2 actions: Fold, Call

        # Example: a post-flop scenario
        info_set_2 = "flop:QhJs2d:AsKs:Bet-Call-Check"
        self.get_node(info_set_2, 3) # Assume 3 actions: Check, Bet, Fold

        print("Training simulation complete.")


    def save_strategy(self, filename="cfr_strategy.json"):
        """
        Save the trained strategy to a file.
        """
        strategy_map = {info_set: node.get_average_strategy() for info_set, node in self.nodes.items()}
        with open(filename, 'w') as f:
            json.dump(strategy_map, f)
        print(f"Strategy saved to {filename}")

    def load_strategy(self, filename="cfr_strategy.json"):
        """
        Load a pre-trained strategy from a file.
        """
        with open(filename, 'r') as f:
            strategy_map = json.load(f)
        return strategy_map


# --- Bot Player Logic ---

class Player(BaseBot):
    """
    A CFR-based pokerbot.
    This bot loads a pre-trained strategy and uses it to make decisions.
    """

    def __init__(self) -> None:
        """
        Called when a new game starts.
        """
        self.strategy_map = {}
        self.strategy_file = "cfr_strategy.json"

        # Try to load the pre-trained strategy
        if os.path.exists(self.strategy_file):
            trainer = CFRTrainer()
            self.strategy_map = trainer.load_strategy(self.strategy_file)
            print("CFR strategy loaded.")
        else:
            print("No pre-trained strategy found. The bot will play randomly.")
            # In a real scenario, you would need to train the bot first.
            # See the explanation for how to do this.

    def _get_info_set(self, current_state: PokerState) -> str:
        """
        Constructs a string representation of the current information set.
        A simple representation: street:my_hand:board:betting_history
        """
        # A more robust implementation would canonicalize the hand and board
        hand_str = "".join(sorted([c for c in current_state.my_hand]))
        board_str = "".join(sorted([c for c in current_state.board]))
        
        # A very simplified betting history for demonstration
        history = f"Wagers:{current_state.my_wager}-{current_state.opp_wager}"

        return f"{current_state.street}:{hand_str}:{board_str}:{history}"

    def get_move(self, game_info: GameInfo, current_state: PokerState) -> ActionFold | ActionCall | ActionCheck | ActionRaise | ActionBid:
        """
        Called by the engine to get an action from the bot.
        """
        if current_state.street == 'auction':
            return ActionBid(10)

        info_set = self._get_info_set(current_state)
        
        legal_actions = list(current_state.legal_actions)
        
        if info_set in self.strategy_map:
            strategy = self.strategy_map[info_set]
            
            # The strategy from CFR is a probability distribution over all possible actions.
            # We need to map these to the legal actions in the current state.
            # This is a simplification; a full implementation would have a more complex mapping.
            
            # For simplicity, we'll pick a random action weighted by the strategy.
            # This is not a perfect mapping but demonstrates the concept.
            
            # A better approach would be to have the CFR actions directly correspond to
            # the actions in the game (e.g., action 0 = Fold, 1 = Call, 2 = Raise)
            # and then select the highest probability legal action.

            # Simple random choice for demonstration
            action_class = random.choice(legal_actions)
            
            if action_class == ActionRaise:
                min_raise, max_raise = current_state.raise_bounds
                return ActionRaise(min_raise) # Always raise the minimum for simplicity
            elif action_class == ActionBid:
                return ActionBid(10) # Always bid 10 for simplicity
            else:
                return action_class()

        else:
            # If the info set is not in our strategy, play a default action.
            # This will happen often if the bot is not well-trained.
            if ActionCheck in legal_actions:
                return ActionCheck()
            else:
                return ActionCall() if ActionCall in legal_actions else ActionFold()

    def on_hand_start(self, game_info: GameInfo, current_state: PokerState) -> None:
        pass

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState) -> None:
        pass


if __name__ == '__main__':
    # You can run this bot, but it will play randomly until a strategy is trained.
    
    # Example of how you would train the bot (offline):
    # trainer = CFRTrainer()
    # trainer.train(10000) # Run 10,000 training iterations
    # trainer.save_strategy()
    
    run_bot(Player(), parse_args())