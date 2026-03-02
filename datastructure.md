# Game Data Structures

This document outlines the key data structures used in the poker bot engine, providing information relevant to developing your bot's strategy.

---

### `GameInfo`

This is a `namedtuple` containing general information about the ongoing game session. It provides context that is persistent across multiple hands.

#### This info can only be used if we define different strategies so currently no use


*   **`bankroll: int`**: Your cumulative chip gain or loss since the beginning of the game.
*   **`time_bank: float`**: The remaining time in seconds your bot has to make decisions throughout the entire match.
*   **`round_num: int`**: The current hand number, counting from 1 up to the total number of rounds (`NUM_ROUNDS`).

```python
# Example usage:
# game_info.bankroll
# game_info.time_bank
# game_info.round_num
```

---

### `PokerState`

This is the primary object that encapsulates the current state of an individual poker hand from your bot's perspective. An updated `PokerState` object is passed to your bot whenever it needs to perform an action.

*   **`is_terminal: bool`**: `True` if the current hand has ended (e.g., all players folded or a showdown occurred), `False` otherwise.
*   **`street: str`**: The current stage of the hand. Possible values include: `'preflop'`, `'flop'`, `'auction'`, `'turn'`, and `'river'`.
*   **`my_hand: list[str]`**: A list of two strings representing your hole cards (e.g., `['Ah', 'Ks']`).
*   **`board: list[str]`**: A list of strings representing the community cards currently on the board.
*   **`opp_revealed_cards: list[str]`**: A list of strings for any of your opponent's cards that have been revealed (e.g., through the auction mechanism). This will be an empty list if no cards have been revealed.
*   **`my_chips: int`**: The number of chips you currently have in your stack.
*   **`opp_chips: int`**: The number of chips your opponent currently has in their stack.
*   **`my_wager: int`**: The total number of chips you have contributed to the pot in the current betting round.
*   **`opp_wager: int`**: The total number of chips your opponent has contributed to the pot in the current betting round.
*   **`pot: int`**: The total number of chips accumulated in the pot for the current hand.
*   **`cost_to_call: int`**: The additional number of chips required for you to match your opponent's current wager.
*   **`is_bb: bool`**: `True` if your bot is the big blind for the current hand, `False` otherwise.
*   **`legal_actions: set`**: A set of `Action` classes representing all legal moves your bot can make in the current state (e.g., `{ActionCheck, ActionRaise}`).
*   **`payoff: int`**: Your chip gain or loss for the current hand. This will only be non-zero when `is_terminal` is `True`.
*   **`raise_bounds: tuple[int, int]`**: A tuple indicating the minimum and maximum `amount` values you can use when performing an `ActionRaise`.

```python
# Example usage:
# current_state.street
# current_state.my_hand
# current_state.my_chips
# if current_state.can_act(ActionCall):
#     # ...
```

---

### Action `namedtuple`s

These are simple immutable objects used to specify the action your bot wants to take. They are defined in `pkbot/actions.py`.

*   **`ActionFold()`**: Represents folding your hand.
*   **`ActionCall()`**: Represents calling the current bet.
*   **`ActionCheck()`**: Represents checking (passing the action).
*   **`ActionRaise(amount: int)`**: Represents raising to a specific `amount`.
*   **`ActionBid(amount: int)`**: Represents placing a bid during the auction street.

```python
# Example usage:
# return ActionRaise(current_state.raise_bounds[0])
# return ActionFold()
```