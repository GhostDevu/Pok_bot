import re
import csv
import eval7
import pandas as pd

def calculate_equity(hero_cards_str, villain_cards_str, board_str):
    """Calculates hero's equity using eval7. Returns None if villain cards are unknown."""
    if not hero_cards_str or not villain_cards_str:
        return None
        
    try:
        hero = [eval7.Card(c) for c in hero_cards_str.split()]
        villain = [eval7.Card(c) for c in villain_cards_str.split()]
        board = [eval7.Card(c) for c in board_str.split()] if board_str else []
        
        # Exact calculation using eval7's py_hand_vs_range_exact
        # We treat the villain's specific hand as a range of 1 combo
        villain_range = eval7.HandRange(villain_cards_str.replace(" ", ""))
        equity = eval7.py_hand_vs_range_exact(hero, villain_range, board)
        return round(equity, 4)
    except Exception:
        return None

def parse_pokerbot_log(log_path, hero_name="BotA"):
    hands_data = []
    current_hand = {}
    
    # Common Regex Patterns (You may need to tweak these slightly based on exact engine output)
    round_re = re.compile(r"Round\s+(\d+)")
    dealt_re = re.compile(r"(.*?)\s+dealt\s+(.*)")
    board_re = re.compile(r"(Flop|Turn|River).*?:\s+(.*)")
    bid_re = re.compile(r"(.*?)\s+bids\s+(\d+)")
    action_re = re.compile(r"(.*?)\s+(calls|raises|checks|folds)")
    bankroll_re = re.compile(r"(.*?)\s+bankroll:\s+(-?\d+)")
    
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            # 1. Detect New Round
            m_round = round_re.search(line)
            if m_round:
                if current_hand:
                    # Calculate equities before saving
                    current_hand['equity'] = calculate_equity(
                        current_hand.get('hero_cards', ''),
                        current_hand.get('villain_cards', ''),
                        current_hand.get('board', '')
                    )
                    hands_data.append(current_hand)
                
                current_hand = {
                    'round_num': int(m_round.group(1)),
                    'hero_cards': '',
                    'villain_cards': '',
                    'board': '',
                    'hero_bid': 0,
                    'villain_bid': 0,
                    'vpip': False,
                    'pfr': False,
                    'hero_profit': 0
                }
                continue
                
            if not current_hand:
                continue
                
            # 2. Extract Hole Cards
            m_dealt = dealt_re.search(line)
            if m_dealt:
                player, cards = m_dealt.groups()
                if hero_name in player:
                    current_hand['hero_cards'] = cards.strip()
                else:
                    current_hand['villain_cards'] = cards.strip()

            # 3. Extract Board Cards
            m_board = board_re.search(line)
            if m_board:
                stage, cards = m_board.groups()
                current_hand['board'] += (" " if current_hand['board'] else "") + cards.strip()
                
            # 4. Extract Sneak Peek Auction Bids
            m_bid = bid_re.search(line)
            if m_bid:
                player, amount = m_bid.groups()
                if hero_name in player:
                    current_hand['hero_bid'] = int(amount)
                else:
                    current_hand['villain_bid'] = int(amount)
                    
            # 5. Track Preflop Stats (VPIP & PFR)
            if not current_hand['board']: # If board is empty, we are preflop
                m_action = action_re.search(line)
                if m_action:
                    player, action = m_action.groups()
                    if hero_name in player:
                        if action in ['calls', 'raises']:
                            current_hand['vpip'] = True
                        if action == 'raises':
                            current_hand['pfr'] = True

            # 6. Extract Bankroll / Profit
            m_br = bankroll_re.search(line)
            if m_br:
                player, br = m_br.groups()
                if hero_name in player:
                    # If the engine logs cumulative bankroll, we calculate delta
                    current_bankroll = int(br)
                    if len(hands_data) > 0:
                        prev_bankroll = hands_data[-1].get('cumulative_br', 0)
                        current_hand['hero_profit'] = current_bankroll - prev_bankroll
                    else:
                        current_hand['hero_profit'] = current_bankroll
                    current_hand['cumulative_br'] = current_bankroll

    # Append the final hand
    if current_hand:
        current_hand['equity'] = calculate_equity(
            current_hand.get('hero_cards', ''),
            current_hand.get('villain_cards', ''),
            current_hand.get('board', '')
        )
        hands_data.append(current_hand)

    return hands_data

# --- Execute and Analyze ---
if __name__ == "__main__":
    LOG_FILE = "logs/20260301-131511-821218.glog"
    BOT_NAME = "BotA"

    parsed_hands = parse_pokerbot_log(LOG_FILE, BOT_NAME)
    df = pd.DataFrame(parsed_hands)
    
    # Save raw data to CSV for manual inspection
    df.to_csv("BotA_parsed_stats.csv", index=False)
    
    # Calculate Macro Statistics
    total_hands = len(df)
    if total_hands > 0:
        vpip_rate = (df['vpip'].sum() / total_hands) * 100
        pfr_rate = (df['pfr'].sum() / total_hands) * 100
        total_profit = df['hero_profit'].sum()
        avg_bid = df[df['hero_bid'] > 0]['hero_bid'].mean()
        
        print(f"--- Statistics for {BOT_NAME} ---")
        print(f"Total Hands Played: {total_hands}")
        print(f"Net Profit: {total_profit} chips")
        print(f"VPIP (Voluntarily Put In Pot): {vpip_rate:.1f}%")
        print(f"PFR (Preflop Raise): {pfr_rate:.1f}%")
        print(f"Average Auction Bid (when > 0): {avg_bid:.1f} chips")
        
        # Show Top 5 Most Profitable Hands
        print("\nTop 5 Most Profitable Hands:")
        print(df.nlargest(5, 'hero_profit')[['round_num', 'hero_cards', 'board', 'hero_profit', 'equity']])