import pandas as pd
import re
import logging
from itertools import product
from datetime import datetime

# =========================================================
# CONFIGURATION: Production Logging
# =========================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Industry4.0_Backend")

# =========================================================
# STEP 1: PARSING AND EXTRACTION
# =========================================================
def inverse_generate_ecdv(ecdv_string: str) -> tuple[pd.DataFrame, str, str]:
    if not isinstance(ecdv_string, str):
        return pd.DataFrame(), "", ""

    ecdv_string = ecdv_string.strip()
    
    if not ecdv_string or ecdv_string == "No combinations for this product line":
        return pd.DataFrame(), "", ""

    if not ecdv_string.endswith("*"):
        raise ValueError(f"Invalid ECDV format (missing '*'): {ecdv_string}")
    
    ecdv_string = ecdv_string[:-1]
    match = re.match(r'^([^.]+)\.([A-Za-z0-9]+)(.*)$', ecdv_string)

    if not match:
        raise ValueError("Invalid ECDV structure.")

    CM = match.group(1)
    Family = match.group(2)
    remainder = match.group(3)

    if remainder.startswith("."):
        remainder = remainder[1:]

    if not remainder:
        return pd.DataFrame([{}]), CM, Family

    if "<" in remainder:
        common_str, body = remainder.split("<", 1)
        common_parts = re.findall(r"\([A-Z0-9]+[A-Z0-9]{2}\)|[A-Z0-9]+[A-Z0-9]{2}", common_str)
    else:
        common_parts = []
        body = remainder

    combinations = body.split("/") if body else []
    parsed_rows = []

    for combo in combinations:
        combo = combo.strip()
        if not combo:
            continue
        row_dict = {}
        tokens = re.findall(r"\([A-Z0-9]+[A-Z0-9]{2}\)|[A-Z0-9]+[A-Z0-9]{2}", combo)

        for token in tokens:
            is_exception = False
            if token.startswith("("):
                is_exception = True
                token = token[1:-1]

            col = token[:-2]
            val = token[-2:]
            if is_exception: val = f"!{val}"

            if col in row_dict:
                existing = row_dict[col]
                if not isinstance(existing, list): existing = [existing]
                existing.append(val)
                row_dict[col] = existing
            else:
                row_dict[col] = val
        parsed_rows.append(row_dict)

    if not parsed_rows:
        return pd.DataFrame([{}]), CM, Family

    for row in parsed_rows:
        for part in common_parts:
            is_exception = False
            if part.startswith("("):
                is_exception = True
                part = part[1:-1]
            col = part[:-2]
            val = part[-2:]
            if is_exception: val = f"!{val}"
            
            if col in row:
                existing = row[col]
                if not isinstance(existing, list): existing = [existing]
                existing.append(val)
                row[col] = existing
            else:
                row[col] = val

    all_columns = sorted({col for row in parsed_rows for col in row.keys()})
    final_rows = [{col: row.get(col, []) for col in all_columns} for row in parsed_rows]

    return pd.DataFrame(final_rows), CM, Family

def process_index_simultaneously(idx, old_p_list, old_e_list, new_p_list, new_e_list):
    if not (len(old_p_list) == len(old_e_list) == len(new_p_list) == len(new_e_list)):
        raise ValueError("Critical Error: Input lists must be of the same length.")

    old_p, old_e = old_p_list[idx], old_e_list[idx]
    new_p, new_e = new_p_list[idx], new_e_list[idx]

    if bool(old_p) != bool(old_e):
        raise ValueError(f"Integrity Error at index {idx}: Mismatch in Old Product/ECDV pair.")
    if bool(new_p) != bool(new_e):
        raise ValueError(f"Integrity Error at index {idx}: Mismatch in New Product/ECDV pair.")

    old_df, old_CM, old_Family = inverse_generate_ecdv(old_e)
    new_df, new_CM, new_Family = inverse_generate_ecdv(new_e)

    # Smart extraction: Fallback to new if old is empty (Creation case)
    CM = old_CM if old_CM else new_CM
    Family = old_Family if old_Family else new_Family

    return old_df, new_df, CM, Family

# =========================================================
# STEP 2: DELTA IDENTIFICATION & WINDOW PREP
# =========================================================
def get_window_elements(quarter_str: str) -> tuple[str, str]:
    quarter_str = quarter_str.strip().upper()
    q_letter = quarter_str[0]
    year_str = quarter_str[1:]
    input_year = int(year_str)
    
    quarter_map = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    input_q_val = quarter_map[q_letter]
    ref_year = 2020
    idx = (input_year - ref_year) * 4 + input_q_val
    
    prefixes = ["W4", "R7", "R0", "R8", "V7", "V8", "V0", "V9"]
    half = 4
    block_size = 8
    
    block = idx // block_size
    pos = idx % block_size
    prefix = prefixes[pos]
    
    base_value = 10 + block if pos < half else 9 + block
    opening_window = f"{prefix}{base_value:02d}"
    closing_window = f"{prefix}{(base_value - 1):02d}"
    
    return opening_window, closing_window

def align_dataframes(df1: pd.DataFrame, df2: pd.DataFrame):
    if df1.empty and df2.empty:
        return df1.copy(), df2.copy()
    all_columns = sorted(set(df1.columns).union(set(df2.columns)))
    df1_aligned = df1.copy()
    df2_aligned = df2.copy()
    for col in all_columns:
        if col not in df1_aligned.columns:
            df1_aligned[col] = [[] for _ in range(len(df1_aligned))]
        if col not in df2_aligned.columns:
            df2_aligned[col] = [[] for _ in range(len(df2_aligned))]
    return df1_aligned[all_columns], df2_aligned[all_columns]

def identify_changed_combinations(old_p: str, old_df: pd.DataFrame, new_p: str, new_df: pd.DataFrame):
    if not old_p and not new_p: return pd.DataFrame(), pd.DataFrame()
    if not old_p and new_p: return pd.DataFrame(), new_df.copy()
    if old_p and not new_p: return old_df.copy(), pd.DataFrame()
    if old_p != new_p: return old_df.copy(), new_df.copy()

    old_changed_comb, new_changed_comb = align_dataframes(old_df, new_df)
    columns = old_changed_comb.columns
    to_drop_old, to_drop_new = set(), set()

    for i, row1 in old_changed_comb.iterrows():
        for j, row2 in new_changed_comb.iterrows():
            if j in to_drop_new: continue
            is_identical = True
            for col in columns:
                v1, v2 = row1[col], row2[col]
                list1 = v1 if isinstance(v1, list) else ([v1] if pd.notna(v1) and v1 != "" else [])
                list2 = v2 if isinstance(v2, list) else ([v2] if pd.notna(v2) and v2 != "" else [])
                if set(list1) != set(list2):
                    is_identical = False
                    break
            if is_identical:
                to_drop_old.add(i)
                to_drop_new.add(j)
                break

    final_old = old_changed_comb.drop(index=list(to_drop_old)).reset_index(drop=True)
    final_new = new_changed_comb.drop(index=list(to_drop_new)).reset_index(drop=True)
    return final_old, final_new

# =========================================================
# STEP 3: WINDOW INJECTION & MERGING
# =========================================================
def apply_window_elements(
    final_old, final_new, DAN_date, 
    old_versions_start_date, old_below_above, 
    new_versions_start_date, new_below_above, 
    opening_window, closing_window
):
    if len(final_old) != len(old_versions_start_date) or len(final_old) != len(old_below_above):
        raise ValueError(f"Length mismatch: final_old({len(final_old)}) vs inputs.")
    if len(final_new) != len(new_versions_start_date) or len(final_new) != len(new_below_above):
        raise ValueError(f"Length mismatch: final_new({len(final_new)}) vs inputs.")

    df_old, df_new = final_old.copy(), final_new.copy()
    below_flags = {'B', 'b', 'below', 'Below', 'BELOW'}

    def inject_window(df, row_idx, window_str):
        if not window_str or len(window_str) < 3: return
        col_name, cell_val = window_str[:2], window_str[2:]
        if col_name not in df.columns: df[col_name] = [[] for _ in range(len(df))]
        current_cell = df.at[row_idx, col_name]
        if not isinstance(current_cell, list):
            current_cell = [current_cell] if pd.notna(current_cell) and current_cell != "" else []
        if cell_val not in current_cell:
            new_list = current_cell.copy()
            new_list.append(cell_val)
            df.at[row_idx, col_name] = new_list

    # Convert DAN date to a datetime object if it's a string, for safe comparison
    parsed_DAN = None
    if DAN_date:
        if isinstance(DAN_date, str):
            try: parsed_DAN = datetime.strptime(DAN_date, "%Y-%m-%d")
            except: pass
        else:
            parsed_DAN = DAN_date

    def is_below_logic(flag_val, d_val, dan):
        if flag_val is not None and str(flag_val).strip() in below_flags: return True
        if d_val and dan:
            try:
                # Basic parsing to compare dates
                parsed_d = datetime.strptime(str(d_val).strip(), "%Y-%m-%d") if isinstance(d_val, str) else d_val
                if parsed_d < dan: return True
            except: pass
        return False

    for i in range(len(df_old)):
        if is_below_logic(old_below_above[i], old_versions_start_date[i], parsed_DAN):
            inject_window(df_old, i, closing_window)

    for i in range(len(df_new)):
        if is_below_logic(new_below_above[i], new_versions_start_date[i], parsed_DAN):
            inject_window(df_new, i, opening_window)

    return df_old, df_new

def filter_old_combinations(df_old: pd.DataFrame, closing_window: str) -> pd.DataFrame:
    if df_old.empty or not closing_window or len(closing_window) < 3:
        return pd.DataFrame(columns=df_old.columns)
    col_name, cell_val = closing_window[:2], closing_window[2:]
    if col_name not in df_old.columns:
        return pd.DataFrame(columns=df_old.columns)
    
    mask = df_old[col_name].apply(lambda cell: isinstance(cell, list) and cell_val in cell)
    return df_old[mask].reset_index(drop=True)

# =========================================================
# GENERATE ECDV (As provided in instructions)
# =========================================================
def generate_ecdv(df: pd.DataFrame, CM: str, Family: str) -> str:
    if df.empty: return ""
    df = df.copy()

    VT_CM_MAP = {'CJ': '09', '88': '02', '89': '01', '82': '04', 'FV': '07', 'FL': '11', 'EL': '49', 'EN': '47', 'GL': '48', 'RL': '46', 'VB': '36', 'VN': '44', '76': '21'}

    if 'VT' in df.columns:
        expected_vt = VT_CM_MAP.get(str(CM))
        if expected_vt is None: raise ValueError(f"CM '{CM}' not defined in VT mapping.")
        def valid_VT(val):
            if isinstance(val, list): return len(val) == 0
            if pd.isna(val): return True
            return str(val).zfill(2) == expected_vt
        df = df[df['VT'].apply(valid_VT)]
        df = df.drop(columns=['VT'])

    if 'A' in df.columns:
        expected_A = str(Family[0]).zfill(2)
        def valid_A(val):
            if isinstance(val, list): return len(val) == 0
            if pd.isna(val): return True
            return str(val).zfill(2) == expected_A
        df = df[df['A'].apply(valid_A)]
        df = df.drop(columns=['A'])

    if 'C' in df.columns:
        expected_C = Family[2:4]
        def valid_C(val):
            if isinstance(val, list): return len(val) == 0
            if pd.isna(val): return True
            return str(val) == expected_C
        df = df[df['C'].apply(valid_C)]
        df = df.drop(columns=['C'])

    family_second_char = Family[1] if len(Family) > 1 else ""
    valid_values = {"01", "0V"} if family_second_char == "G" else {f"0{family_second_char}"}

    for col in ['B', 'ZZ']:
        if col in df.columns:
            def valid_B(val):
                if isinstance(val, list): return len(val) == 0
                if pd.isna(val): return True
                return str(val).zfill(2) in valid_values
            df = df[df[col].apply(valid_B)]
            if col == 'B': df = df.drop(columns=['B'])

    def normalize_value(v):
        s = str(v)
        return s.zfill(2) if s.isdigit() and len(s) == 1 else s

    common_parts, non_common_columns = [], []

    for col in df.columns:
        column_values = df[col].tolist()
        normalized_rows = []
        for val in column_values:
            if isinstance(val, list): normalized_rows.append([normalize_value(v) for v in val])
            elif pd.isna(val): normalized_rows.append([])
            else: normalized_rows.append([normalize_value(val)])

        if not normalized_rows: continue
        common_elements = set(normalized_rows[0]).intersection(*[set(r) for r in normalized_rows[1:]])

        if common_elements:
            for el in sorted(list(common_elements)):
                if el.startswith("!"): common_parts.append(f"({col}{el[1:]})")
                else: common_parts.append(f"{col}{el}")
            new_col_values = []
            has_leftovers = False
            for r in normalized_rows:
                leftovers = [v for v in r if v not in common_elements]
                new_col_values.append(leftovers)
                if leftovers: has_leftovers = True
            df[col] = new_col_values
            if has_leftovers: non_common_columns.append(col)
        else:
            df[col] = normalized_rows
            if any(normalized_rows): non_common_columns.append(col)

    result = []
    for row_index, row in df.iterrows():
        values = []
        for col in non_common_columns:
            val = row[col]
            if isinstance(val, list):
                if len(val) == 0: continue
            else:
                if pd.isna(val): continue
                val = [val]
            val = [normalize_value(v) for v in val]
            normal_vals = [v for v in val if not v.startswith("!")]
            exception_vals = [v for v in val if v.startswith("!")]
            if normal_vals and exception_vals: raise ValueError(f"Mixed include/exclude in col '{col}'")
            if exception_vals:
                grouped = "".join(f"({col}{v[1:]})" for v in exception_vals)
                values.append([grouped])
            elif normal_vals:
                values.append([f"{col}{v}" for v in normal_vals])

        if not values: continue
        for combo in product(*values):
            formatted = ""
            for part in combo:
                if part.startswith("("): formatted += part
                else:
                    if formatted and not formatted.endswith(")"): formatted += "."
                    formatted += part
            result.append(formatted)

    body = "/".join(result)

    def build_common_string(parts):
        formatted = ""
        for part in parts:
            if part.startswith("("): formatted += part
            else:
                if formatted and not formatted.endswith(")"): formatted += "."
                formatted += part
        return formatted

    common_str = build_common_string(common_parts)
    if not common_parts and not body: return "No combinations for this product line"

    first_char = common_str[0] if common_parts else (body[0] if body else "")
    prefix = f"{CM}.{Family}" if first_char == "(" else f"{CM}.{Family}."

    if common_parts and body:
        return f"{prefix}{common_str}<{body}*" if len(result) > 1 else f"{prefix}{common_str}{body}*"
    elif common_parts and not body:
        return f"{prefix}{common_str}*"
    else:
        return f"{prefix}{body}*"

def execute_step_3_merging(old_p: str, df_old: pd.DataFrame, new_p: str, df_new: pd.DataFrame, CM: str, Family: str) -> dict:
    old_exists = bool(old_p and str(old_p).strip())
    new_exists = bool(new_p and str(new_p).strip())
    
    results = {"old_ecdv_output": None, "new_ecdv_output": None, "case_executed": None}

    if old_exists and not new_exists:
        results["old_ecdv_output"] = generate_ecdv(df_old, CM, Family)
        results["case_executed"] = "Case 4 (Cancellation)"
    elif new_exists and not old_exists:
        results["new_ecdv_output"] = generate_ecdv(df_new, CM, Family)
        results["case_executed"] = "Case 3 (Creation)"
    elif old_exists and new_exists:
        if str(old_p).strip() != str(new_p).strip():
            results["old_ecdv_output"] = generate_ecdv(df_old, CM, Family)
            results["new_ecdv_output"] = generate_ecdv(df_new, CM, Family)
            results["case_executed"] = "Case 1 (Cancel and Replace)"
        else:
            df_merged = pd.concat([df_old, df_new], ignore_index=True)
            results["new_ecdv_output"] = generate_ecdv(df_merged, CM, Family)
            results["case_executed"] = "Case 2 (ECDV Modification)"
    
    return results

# ==================================================
# UI FORMATTING HELPER FUNCTIONS
# ==================================================

def format_cell_for_display(value):
    """
    Formats individual cells for the UI.
    Converts lists into a clean string format with '+' and '()' for exceptions.
    """
    if isinstance(value, list):
        if len(value) == 0:
            return ""
        lines = []
        for i, v in enumerate(value):
            if isinstance(v, str) and v.startswith("!"):
                v = v[1:]
            prefix = "" if i == 0 else "+"
            lines.append(f"{prefix}({v})")
        return "\n".join(lines)

    if pd.isna(value):
        return ""

    s = str(value)
    if s.startswith("!"):
        return f"({s[1:]})"

    return s

def format_dataframe_for_display(df):
    """
    Applies the cell formatting to the entire DataFrame.
    Returns a copy of the dataframe with all elements safely converted to strings.
    """
    display_df = df.copy()
    for col in display_df.columns:
        display_df[col] = display_df[col].apply(format_cell_for_display)
    return display_df
