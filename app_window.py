import streamlit as st
import pandas as pd


def format_custom_tsv(df: pd.DataFrame) -> str:
    """
    Industry 4.0 Specific Formatter:
    Converts DataFrame to 'ColName+Value' tab-separated string.
    Ignores empty lists [].
    """
    tsv_lines = []
    for _, row in df.iterrows():
        row_elements = []
        for col in df.columns:
            val = str(row[col]).strip()
            # Filter out empty indicators used in your logic
            if val not in ["[]", "['']", "", "nan", "None"]:
                # Logic: Concatenate Column Header + Value (e.g., XF + 01 = XF01)
                row_elements.append(f"{col}{val}")
        if row_elements:
            tsv_lines.append("\t".join(row_elements))
    return "\n".join(tsv_lines)

from backend_window import (
    get_window_elements,
    process_index_simultaneously,
    identify_changed_combinations,
    apply_window_elements,
    filter_old_combinations,
    execute_step_3_merging
)

st.set_page_config(layout="wide")

# ==========================================
# STATE MANAGEMENT
# ==========================================
if 'stage' not in st.session_state:
    st.session_state.stage = 0
if 'delta_results' not in st.session_state:
    st.session_state.delta_results = []

def text_to_list(text):
    """Safely converts multiline text to a list, keeping empty lines as empty strings."""
    if not text: return []
    return [line.strip() for line in text.split('\n')]

# ==========================================
# UI LAYOUT
# ==========================================
st.title("Windows application Tool")

# --- INITIAL INPUTS ---
col1, col2, col3, col4 = st.columns([1, 3, 1, 3])
with col1: old_prod_input = st.text_area("Old Product")
with col2: old_ecdv_input = st.text_area("Old ECDV")
with col3: new_prod_input = st.text_area("New Product")
with col4: new_ecdv_input = st.text_area("New ECDV")

col_date, col_qtr = st.columns([1, 1])
with col_date: dan_date_input = st.text_input("DAN Date (YYYY-MM-DD)")
with col_qtr: quarter_input = st.text_input("Quarter (e.g., A2026)")

# ==========================================
# STAGE 1: DELTA IDENTIFICATION
# ==========================================
if st.button("combinations undergoing change"):
    try:
        new_p_list = text_to_list(new_prod_input)
        new_e_list = text_to_list(new_ecdv_input)
        old_p_list = text_to_list(old_prod_input)
        old_e_list = text_to_list(old_ecdv_input)

        # Pad lists to same length to prevent zipper errors
        max_len = max(len(new_p_list), len(new_e_list), len(old_p_list), len(old_e_list))
        new_p_list += [""] * (max_len - len(new_p_list))
        new_e_list += [""] * (max_len - len(new_e_list))
        old_p_list += [""] * (max_len - len(old_p_list))
        old_e_list += [""] * (max_len - len(old_e_list))

        opening_win, closing_win = get_window_elements(quarter_input)
        
        results_cache = []
        for idx in range(max_len):
            old_df, new_df, CM, Family = process_index_simultaneously(
                idx, old_p_list, old_e_list, new_p_list, new_e_list
            )
            final_old, final_new = identify_changed_combinations(
                old_p_list[idx], old_df, new_p_list[idx], new_df
            )
            results_cache.append({
                "idx": idx, "CM": CM, "Family": Family,
                "old_p": old_p_list[idx], "new_p": new_p_list[idx],
                "final_old": final_old, "final_new": final_new
            })
            
        st.session_state.delta_results = results_cache
        st.session_state.opening_win = opening_win
        st.session_state.closing_win = closing_win
        st.session_state.stage = 1
        st.success("Delta processed successfully!")
    except Exception as e:
        st.error(f"Error processing deltas: {str(e)}")

# ==========================================
# STAGE 1 UI: HUMAN-IN-THE-LOOP
# ==========================================
if st.session_state.stage >= 1:
    st.markdown("---")
    user_inputs = {} # Store user inputs locally to process on next button click

    for res in st.session_state.delta_results:
        idx = res["idx"]
        st.subheader(f"Index {idx} | Products: {res['old_p']} -> {res['new_p']}")
        
        # --- OLD DATAFRAME UI ---
        if not res["final_old"].empty:
            st.markdown("**Final Old Combinations**")
            cols_old = st.columns([3, 1, 1])
            with cols_old[0]:
                st.dataframe(res["final_old"], use_container_width=True)
            
            # Use fixed height to align multiline text boxes roughly with table
            table_height = len(res["final_old"]) * 35 + 40 
            with cols_old[1]:
                user_inputs[f"old_ba_{idx}"] = st.text_area(
                    "below or above", key=f"oba_{idx}", height=table_height
                )
            with cols_old[2]:
                user_inputs[f"old_vsd_{idx}"] = st.text_area(
                    "versions start date", key=f"ovsd_{idx}", height=table_height
                )

            with st.expander("📋 Copy Table for Excel (Old)"):
                # Use our new formatter instead of standard to_csv
                custom_tsv_old = format_custom_tsv(res["final_old"])
                st.code(custom_tsv_old, language="text")
        
        # --- NEW DATAFRAME UI ---
        if not res["final_new"].empty:
            st.markdown("**Final New Combinations**")
            cols_new = st.columns([3, 1, 1])
            with cols_new[0]:
                st.dataframe(res["final_new"], use_container_width=True)
            
            table_height = len(res["final_new"]) * 35 + 40
            with cols_new[1]:
                user_inputs[f"new_ba_{idx}"] = st.text_area(
                    "below or above", key=f"nba_{idx}", height=table_height
                )
            with cols_new[2]:
                user_inputs[f"new_vsd_{idx}"] = st.text_area(
                    "versions start date", key=f"nvsd_{idx}", height=table_height
                )

            with st.expander("📋 Copy Table for Excel (New)"):
                # Use our new formatter instead of standard to_csv
                custom_tsv_new = format_custom_tsv(res["final_new"])
                st.code(custom_tsv_new, language="text")
        
        st.markdown("---")

    # ==========================================
    # STAGE 2: WINDOWS TREATMENT
    # ==========================================
    if st.button("windows treatment"):
        try:
            final_summaries = []
            
            for res in st.session_state.delta_results:
                idx = res["idx"]
                
                # Fetch lists, pad with None to match dataframe length if user missed a line
                def get_padded_list(key, length):
                    raw = text_to_list(user_inputs.get(key, ""))
                    return raw + [None] * (length - len(raw))

                old_len, new_len = len(res["final_old"]), len(res["final_new"])
                
                o_ba = get_padded_list(f"old_ba_{idx}", old_len)
                o_vsd = get_padded_list(f"old_vsd_{idx}", old_len)
                n_ba = get_padded_list(f"new_ba_{idx}", new_len)
                n_vsd = get_padded_list(f"new_vsd_{idx}", new_len)

                # 1. Apply Windows
                df_old, df_new = apply_window_elements(
                    res["final_old"], res["final_new"], dan_date_input,
                    o_vsd, o_ba, n_vsd, n_ba,
                    st.session_state.opening_win, st.session_state.closing_win
                )
                
                # 2. Filter Old
                filtered_df = filter_old_combinations(df_old, st.session_state.closing_win)
                
                # 3. Merge and Generate
                merge_results = execute_step_3_merging(
                    res["old_p"], filtered_df, res["new_p"], df_new,
                    res["CM"], res["Family"]
                )
                
                # Compile Output Format
                summary_row = {
                    "Case": merge_results["case_executed"],
                    "Old Product": res["old_p"],
                    "Old String Output": merge_results["old_ecdv_output"],
                    "New Product": res["new_p"],
                    "New String Output": merge_results["new_ecdv_output"]
                }
                final_summaries.append(summary_row)

            # Display Final Tabular Data
            st.success("Windows Treatment Applied Successfully!")
            st.title("Final Operations Output")
            final_df = pd.DataFrame(final_summaries)
            
            # Using st.data_editor makes it very easy to view/copy the final result
            st.dataframe(final_df, use_container_width=True)
            
        except Exception as e:
            st.error(f"Error during windows treatment: {str(e)}")
