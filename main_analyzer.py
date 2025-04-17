import pandas as pd
import logging
from urllib.parse import urlparse, urlunparse
import time
import os
from datetime import datetime

# Import functions from our refactored module
from functions import (
    get_organic_results,
    assess_category_page_relevance,
    assess_product_page_relevance,
    classify_and_assess_url # Import the new function
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration ---
TARGET_SITE = "fatshackvintage.com.au"  # Domain only
TARGET_SITE_URL_BASE = f"https://www.{TARGET_SITE}/"

# Known PLP paths (adjust as needed)
KNOWN_PLP_PATHS = [
    f"https://www.{TARGET_SITE}/shop-by-category/",
    f"https://www.{TARGET_SITE}/shop-all-products/",
    f"https://www.{TARGET_SITE}/shop-all/",
    f"https://www.{TARGET_SITE}/collections"
]

# Known PDP paths (adjust as needed)
KNOWN_PDP_PATHS = [
    f"https://www.{TARGET_SITE}/products/"
]

# Known Irrelevant paths (adjust as needed - ensure they end with / if they are directories)
KNOWN_IRRELEVANT_PATHS = [
    f"https://www.{TARGET_SITE}/articles/",
    f"https://www.{TARGET_SITE}/help/",
    f"https://www.{TARGET_SITE}/about-us/",
    f"https://www.{TARGET_SITE}/contact-us/",
    f"https://www.{TARGET_SITE}/" # Homepage
]

# Load known PLPs from Bloom's data (optional, but recommended for accuracy)
KNOWN_PLPS_CSV_PATH = ''  # Path to your CSV

# Function to load keywords from a file
def load_keywords_from_file(filepath):
    """Loads keywords from a file, trims whitespace, removes duplicates and empty lines."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            # Read lines, strip whitespace, filter out empty lines
            keywords = [line.strip() for line in f if line.strip()]
        # Deduplicate while preserving order (Python 3.7+)
        unique_keywords = list(dict.fromkeys(keywords))
        logging.info(f"Loaded {len(unique_keywords)} unique keywords from {filepath}")
        return unique_keywords
    except FileNotFoundError:
        logging.error(f"Keyword file not found at {filepath}. Please create it.")
        return []
    except Exception as e:
        logging.error(f"Error loading keywords from {filepath}: {e}")
        return []

# Load keywords from the file
KEYWORD_FILE_PATH = 'keywords.txt' # Define the path to your keyword file
KEYWORD_LIST = load_keywords_from_file(KEYWORD_FILE_PATH)
KEYWORD_LIST = KEYWORD_LIST[:25]

# API Call Delay (seconds)
DELAY_BETWEEN_KEYWORDS = 2
DELAY_BETWEEN_URL_ASSESSMENTS = 1

# Output file base name (timestamp and .csv will be added)
OUTPUT_FILENAME_BASE = 'outputs/category_opportunity_analysis'
# --- End Configuration ---

def load_known_plps(csv_path):
    """Loads and cleans known PLP URLs from a CSV file."""
    known_plps = []
    try:
        df = pd.read_csv(csv_path, encoding='latin1')
        # Ensure URLs are clean (no query params/fragments)
        known_plps = df['URL'].astype(str).apply(lambda url: clean_url(url)).unique().tolist()
        logging.info(f"Loaded {len(known_plps)} known PLPs from {csv_path}")
    except FileNotFoundError:
        logging.warning(f"Known PLPs file not found at {csv_path}. Proceeding without it.")
    except Exception as e:
        logging.error(f"Error loading known PLPs from {csv_path}: {e}")
    return known_plps

def clean_url(url):
    """Removes query parameters and fragments from a URL."""
    if not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
        # Reconstruct URL without query and fragment
        scheme = parsed.scheme if parsed.scheme else 'https'
        netloc = parsed.netloc if parsed.netloc else TARGET_SITE
        # Ensure path starts with / if it exists
        path = parsed.path if parsed.path.startswith('/') else '/' + parsed.path
        if path == '/': path = '' # Avoid trailing slash if path is root

        return urlunparse(parsed._replace(scheme=scheme, netloc=netloc, path=path, params='', query='', fragment=''))
    except Exception as e:
        logging.warning(f"Could not parse or clean URL '{url}': {e}")
        return None

def classify_url(url, known_plps_from_csv):
    """Classifies a URL as 'Known PLP', 'Known PDP', 'Irrelevant', or 'Unknown' based on configured paths."""
    cleaned_url = clean_url(url)
    if not cleaned_url:
        return 'Unknown' # Cannot classify if cleaning failed

    # Check Irrelevant first
    if any(cleaned_url.startswith(path) for path in KNOWN_IRRELEVANT_PATHS):
         # Exact match for homepage
        if cleaned_url == f"https://www.{TARGET_SITE}/":
            return 'Irrelevant'
        # Prefix match for others (ensure it's not just a prefix of a valid page)
        if any(cleaned_url.startswith(path) and len(cleaned_url) > len(path) for path in KNOWN_IRRELEVANT_PATHS if path != f"https://www.{TARGET_SITE}/"):
             return 'Irrelevant'
        # Handle exact match for paths ending in /
        if any(cleaned_url == path for path in KNOWN_IRRELEVANT_PATHS):
             return 'Irrelevant'


    # Check Known PLP (exact match from CSV or prefix)
    if cleaned_url in known_plps_from_csv:
        return 'Known PLP'
    # Check Known PLP (prefix, excluding URLs with both /collections/ and /products/)
    if any(cleaned_url.startswith(path) for path in KNOWN_PLP_PATHS) and \
       not ('/collections/' in cleaned_url and '/products/' in cleaned_url):
        return 'Known PLP'

    # Check Known PDP (prefix OR contains /products/ segment)
    if any(cleaned_url.startswith(path) for path in KNOWN_PDP_PATHS) or '/products/' in cleaned_url:
        return 'Known PDP'

    # If none of the above, classify as Unknown for AI assessment
    return 'Unknown'

def analyze_keywords(keywords, known_plps_list):
    """Processes the list of keywords using the refined workflow."""
    results_list = []

    for keyword in keywords:
        logging.info(f"--- Processing Keyword: '{keyword}' ---")
        # Initialize detailed result dictionary
        keyword_result = {
            'Keyword': keyword,
            'SERP_Results_Found': False,
            'Initial_Classification': {}, # Store initial URL classifications
            'Known_PLP_Assessment': {}, # Store relevance for Known PLPs
            'Known_PDP_Assessment': {}, # Store relevance for Known PDPs
            'Unknown_URL_Assessment': {}, # Store AI type/relevance for Unknowns
            'Decision': 'Error',
            'Justification': 'Processing failed.',
            'SERP_Raw_HTML_URL': None # Add field for raw HTML URL
        }

        # 1. Fetch SERP Results
        # Correctly unpack the two return values
        serp_html_url, serp_df = get_organic_results(keyword, TARGET_SITE)

        if serp_df.empty:
            logging.warning(f"No SERP results found for keyword '{keyword}' on {TARGET_SITE}.")
            keyword_result['Decision'] = 'No (Irrelevant)'
            keyword_result['Justification'] = 'No organic results found for this keyword on the target site.'
            keyword_result['SERP_Raw_HTML_URL'] = serp_html_url # Store even if no results
            results_list.append(keyword_result)
            time.sleep(DELAY_BETWEEN_KEYWORDS)
            continue

        keyword_result['SERP_Results_Found'] = True
        keyword_result['SERP_Raw_HTML_URL'] = serp_html_url # Store the URL
        logging.info(f"Found {len(serp_df)} results in SERP for '{keyword}'.")

        # 2. Initial Classification
        classified_urls = {'Known PLP': [], 'Known PDP': [], 'Irrelevant': [], 'Unknown': []}
        for url in serp_df['Ranking URL']:
            cleaned = clean_url(url)
            if not cleaned: continue
            page_type = classify_url(cleaned, known_plps_list)
            if cleaned not in [item for sublist in classified_urls.values() for item in sublist]: # Avoid duplicates
                 classified_urls[page_type].append(cleaned)

        keyword_result['Initial_Classification'] = {k: v for k, v in classified_urls.items()} # Store counts/lists
        logging.info(f"Initial Classification: Known PLP={len(classified_urls['Known PLP'])}, Known PDP={len(classified_urls['Known PDP'])}, Irrelevant={len(classified_urls['Irrelevant'])}, Unknown={len(classified_urls['Unknown'])}")

        # --- Assessment Stages ---
        known_plp_assessments = {}
        known_pdp_assessments = {}
        unknown_url_assessments = {}
        found_closely_related_plp = False
        best_plp_relevance = None # None -> Unrelated -> Loosely Related -> Closely Related
        found_related_pdp = False

        # 3. Prioritized Assessment (Known PLPs)
        if classified_urls['Known PLP']:
            logging.info(f"Assessing {len(classified_urls['Known PLP'])} Known PLP(s)...")
            for url in classified_urls['Known PLP']:
                time.sleep(DELAY_BETWEEN_URL_ASSESSMENTS)
                result = assess_category_page_relevance(keyword, url)
                if result:
                    known_plp_assessments[url] = result
                    relevance = result.get('Relevant')
                    if relevance == 'Closely Related':
                        found_closely_related_plp = True
                        best_plp_relevance = 'Closely Related'
                        logging.info(f"Found 'Closely Related' Known PLP: {url}. Stopping PLP assessment.")
                        break # Found the best case
                    elif relevance == 'Loosely Related':
                        best_plp_relevance = 'Loosely Related'
                    elif best_plp_relevance is None: # Only set to Unrelated if nothing better found yet
                        best_plp_relevance = 'Unrelated'
                else:
                    known_plp_assessments[url] = {'Relevant': 'Assessment Failed', 'Analysis': 'API call failed.'}
                    if best_plp_relevance is None: best_plp_relevance = 'Unrelated'
            keyword_result['Known_PLP_Assessment'] = known_plp_assessments
            if found_closely_related_plp:
                 keyword_result['Decision'] = 'No (Existing page sufficient)'
                 keyword_result['Justification'] = "A 'Closely Related' Known PLP was found in SERP."
                 results_list.append(keyword_result)
                 logging.info(f"Decision for '{keyword}': {keyword_result['Decision']}")
                 time.sleep(DELAY_BETWEEN_KEYWORDS)
                 continue # Move to next keyword

        # 4. AI Assessment (Unknown URLs) - Only if no Closely Related Known PLP found
        ai_identified_plps = {} # Store AI results for URLs classified as PLP
        ai_identified_pdps = {} # Store AI results for URLs classified as PDP

        if classified_urls['Unknown']:
            logging.info(f"Assessing {len(classified_urls['Unknown'])} Unknown URL(s)...")
            for url in classified_urls['Unknown']:
                time.sleep(DELAY_BETWEEN_URL_ASSESSMENTS)
                result = classify_and_assess_url(keyword, url) # Use the new combined function
                if result:
                    unknown_url_assessments[url] = result
                    determined_type = result.get('determined_type')
                    relevance = result.get('relevance')

                    # Track best PLP relevance from AI results
                    if determined_type == 'PLP' or determined_type == 'Brand Page':
                        ai_identified_plps[url] = result
                        if relevance == 'Closely Related':
                             # This becomes the best PLP if no Known PLP was Closely Related
                            best_plp_relevance = 'Closely Related'
                            found_closely_related_plp = True # Mark this for decision logic
                            logging.info(f"AI identified 'Closely Related' PLP/Brand Page: {url}. Stopping Unknown URL assessment.")
                            break # Stop assessing unknown URLs now
                        elif relevance == 'Loosely Related' and best_plp_relevance != 'Closely Related':
                            best_plp_relevance = 'Loosely Related'
                        elif relevance == 'Unrelated' and best_plp_relevance is None:
                            best_plp_relevance = 'Unrelated'

                    # Track if any related PDPs are found by AI
                    elif determined_type == 'PDP':
                        ai_identified_pdps[url] = result
                        if relevance == 'Related':
                            found_related_pdp = True
                            logging.info(f"AI identified 'Related' PDP: {url}")

                else:
                     unknown_url_assessments[url] = {'determined_type': 'Error', 'relevance': 'Error', 'analysis': 'API call failed.'}
            keyword_result['Unknown_URL_Assessment'] = unknown_url_assessments
            # Re-check if a closely related PLP was found by AI
            if found_closely_related_plp:
                 keyword_result['Decision'] = 'No (Existing page sufficient)'
                 keyword_result['Justification'] = "AI identified a 'Closely Related' PLP or Brand Page in SERP."
                 results_list.append(keyword_result)
                 logging.info(f"Decision for '{keyword}': {keyword_result['Decision']}")
                 time.sleep(DELAY_BETWEEN_KEYWORDS)
                 continue # Move to next keyword

        # 5. Assessment (Known PDPs) - Only if no Closely Related PLP found yet
        if classified_urls['Known PDP']:
             logging.info(f"Assessing {len(classified_urls['Known PDP'])} Known PDP(s)...")
             for url in classified_urls['Known PDP']:
                 time.sleep(DELAY_BETWEEN_URL_ASSESSMENTS)
                 result = assess_product_page_relevance(keyword, url)
                 if result:
                     known_pdp_assessments[url] = result
                     if result.get('Relevance') == 'Related':
                         found_related_pdp = True # Mark if any known PDP is related
                         logging.info(f"Found 'Related' Known PDP: {url}")
                 else:
                     known_pdp_assessments[url] = {'Relevance': 'Assessment Failed', 'Analysis': 'API call failed.'}
             keyword_result['Known_PDP_Assessment'] = known_pdp_assessments

        # 6. Final Decision Logic (Synthesizing) - Only runs if no Closely Related PLP was found
        logging.info(f"Synthesizing decision for '{keyword}': Best PLP Relevance='{best_plp_relevance}', Found Related PDP='{found_related_pdp}'")
        if best_plp_relevance == 'Loosely Related':
            if found_related_pdp:
                keyword_result['Decision'] = 'Yes (Create *specific* category)'
                keyword_result['Justification'] = "Found 'Loosely Related' PLP and 'Related' products/pages, suggesting a more specific category is needed."
            else:
                keyword_result['Decision'] = 'No (Loose PLP is best for now)'
                keyword_result['Justification'] = "Found 'Loosely Related' PLP, but no specific 'Related' products/pages found to justify a new category."
        elif best_plp_relevance == 'Unrelated' or best_plp_relevance is None:
             if found_related_pdp:
                keyword_result['Decision'] = 'Yes (Create *new* category)'
                keyword_result['Justification'] = "No relevant PLP found, but 'Related' products/pages exist, justifying a new category."
             else:
                keyword_result['Decision'] = 'No (No relevant products/pages)'
                keyword_result['Justification'] = 'No relevant PLPs or other pages related to the keyword were found.'
        else: # Should not happen if closely_related check worked
             keyword_result['Decision'] = 'Error'
             keyword_result['Justification'] = 'Unhandled case in final decision logic.'
             logging.error(f"Unhandled final decision logic case for keyword '{keyword}'")

        results_list.append(keyword_result)
        logging.info(f"Decision for '{keyword}': {keyword_result['Decision']}")
        logging.info(f"Waiting {DELAY_BETWEEN_KEYWORDS}s before next keyword...")
        time.sleep(DELAY_BETWEEN_KEYWORDS)

    logging.info("--- Keyword Processing Complete ---")
    return pd.DataFrame(results_list)

def save_results(df, output_base_path):
    """Saves the results DataFrame to a CSV file with a timestamp."""
    try:
        # Generate timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        full_output_path = f"{output_base_path}_{timestamp}.csv"

        # Create outputs directory if it doesn't exist
        os.makedirs(os.path.dirname(full_output_path), exist_ok=True)

        # Select and reorder columns
        # Define columns to include in the CSV output
        output_columns = [
            'Keyword', 'Decision', 'Justification', 'SERP_Results_Found',
            'SERP_Raw_HTML_URL',      # Added
            'Initial_Classification',
            'Known_PLP_Assessment',
            'Known_PDP_Assessment',
            'Unknown_URL_Assessment'
        ]
        # Ensure all expected columns exist in the DataFrame, add if missing (e.g., if script failed early)
        for col in output_columns:
            if col not in df.columns:
                df[col] = None # Add missing column with None values
        export_df = df[output_columns].copy()

        # Convert complex columns to strings for CSV
        # Convert complex columns (dicts) to strings for CSV compatibility
        # Also convert SERP_Raw_HTML_URL if it exists
        complex_cols = ['Initial_Classification', 'Known_PLP_Assessment', 'Known_PDP_Assessment', 'Unknown_URL_Assessment', 'SERP_Raw_HTML_URL']
        for col in complex_cols:
             if col in export_df.columns: # Check if column exists before conversion
                # Use fillna('') before astype(str) to handle potential None values gracefully
                export_df[col] = export_df[col].fillna('').astype(str)

        export_df.to_csv(full_output_path, index=False)
        logging.info(f"Results successfully saved to {full_output_path}")
    except Exception as e:
        logging.error(f"Failed to save results to CSV at {full_output_path}: {e}")


def generate_markdown_report(df, output_base_path):
    """Generates a Markdown report summarizing the analysis results based on the refined workflow."""
    try:
        # Generate timestamp (same as CSV for consistency)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        full_output_path = f"{output_base_path}_{timestamp}.md"

        # Create outputs directory if it doesn't exist
        os.makedirs(os.path.dirname(full_output_path), exist_ok=True)

        md_content = []
        md_content.append(f"# Category Opportunity Analysis Report ({report_timestamp})\n")

        # --- Summary Section ---
        md_content.append("## Summary: Opportunities Identified\n")
        # Ensure 'Decision' column exists before filtering
        if 'Decision' in df.columns:
            opportunities = df[df['Decision'].str.startswith('Yes', na=False)]
            if not opportunities.empty:
                for _, row in opportunities.iterrows():
                    # Check if 'Keyword' and 'Justification' exist
                    keyword = row.get('Keyword', 'N/A')
                    decision = row.get('Decision', 'N/A')
                    justification = row.get('Justification', 'N/A')
                    md_content.append(f"*   **{keyword}**: {decision} - Justification: {justification}")
            else:
                md_content.append("*   No immediate opportunities for new category pages were identified based on this analysis.")
        else:
             md_content.append("*   'Decision' column not found in results, cannot generate summary.")

        md_content.append("\n---\n")

        # --- Detailed Analysis Section ---
        md_content.append("## Detailed Analysis by Keyword\n")
        for index, row in df.iterrows():
            keyword = row.get('Keyword', 'N/A')
            decision = row.get('Decision', 'N/A')
            justification = row.get('Justification', 'N/A')
            serp_found = row.get('SERP_Results_Found', False)
            serp_html_url = row.get('SERP_Raw_HTML_URL', 'N/A') # Get the HTML URL

            md_content.append(f"### Keyword: \"{keyword}\"\n")
            md_content.append(f"*   **Final Decision:** {decision}")
            md_content.append(f"*   **Justification:** {justification}")
            md_content.append(f"*   **SERP Results:** {'Found' if serp_found else 'Not Found'}")
            md_content.append(f"*   **SERP Raw HTML:** {serp_html_url if serp_html_url else 'N/A'}") # Add HTML URL to report

            # Initial Classification Counts (if available)
            initial_class = row.get('Initial_Classification', {})
            if isinstance(initial_class, dict):
                 md_content.append(f"*   **Initial URL Classification:** "
                                   f"Known PLP: {len(initial_class.get('Known PLP', []))}, "
                                   f"Known PDP: {len(initial_class.get('Known PDP', []))}, "
                                   f"Irrelevant: {len(initial_class.get('Irrelevant', []))}, "
                                   f"Unknown: {len(initial_class.get('Unknown', []))}")

            # Known PLP Assessment
            md_content.append("*   **Known PLP Assessment:**")
            known_plp_scores = row.get('Known_PLP_Assessment', {})
            if isinstance(known_plp_scores, dict) and known_plp_scores:
                for url, score_data in known_plp_scores.items():
                    md_content.append(f"    *   `{url}`")
                    md_content.append(f"        *   Relevance: `{score_data.get('Relevant', 'N/A')}`")
                    md_content.append(f"        *   Analysis: `{score_data.get('Analysis', 'N/A')}`")
            else:
                md_content.append("    *   None assessed or found.")

            # Known PDP Assessment
            md_content.append("*   **Known PDP Assessment:**")
            known_pdp_scores = row.get('Known_PDP_Assessment', {})
            if isinstance(known_pdp_scores, dict) and known_pdp_scores:
                 for url, score_data in known_pdp_scores.items():
                    md_content.append(f"    *   `{url}`")
                    md_content.append(f"        *   Relevance: `{score_data.get('Relevance', 'N/A')}`")
                    md_content.append(f"        *   Analysis: `{score_data.get('Analysis', 'N/A')}`")
            else:
                md_content.append("    *   None assessed or found.")

            # Unknown URL Assessment (AI Classification & Relevance)
            md_content.append("*   **Unknown URL Assessment (AI):**")
            unknown_scores = row.get('Unknown_URL_Assessment', {})
            if isinstance(unknown_scores, dict) and unknown_scores:
                 for url, score_data in unknown_scores.items():
                    md_content.append(f"    *   `{url}`")
                    md_content.append(f"        *   AI Determined Type: `{score_data.get('determined_type', 'N/A')}`")
                    md_content.append(f"        *   Relevance: `{score_data.get('relevance', 'N/A')}`")
                    md_content.append(f"        *   Analysis: `{score_data.get('analysis', 'N/A')}`")
            else:
                md_content.append("    *   None assessed or found.")


            md_content.append("\n---\n") # Separator between keywords

        # Write to file
        with open(full_output_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(md_content))
        logging.info(f"Markdown report successfully saved to {full_output_path}")

    except Exception as e:
        logging.error(f"Failed to generate or save Markdown report: {e}", exc_info=True) # Added full path removal

# --- Main Execution ---
if __name__ == "__main__":
    logging.info("Starting Category Opportunity Analyzer...")

    # Load known PLPs
    known_plps_list = load_known_plps(KNOWN_PLPS_CSV_PATH)

    # Analyze keywords
    final_df = analyze_keywords(KEYWORD_LIST, known_plps_list)

    # Display results
    logging.info("--- Final Results ---")
    pd.set_option('display.max_rows', None)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_colwidth', None)
    # Select columns for the simplified console printout
    print_columns = [
        'Keyword', 'Decision', 'Justification', 'SERP_Results_Found'
    ]
    # Filter final_df to only include existing columns from print_columns
    print_df = final_df[[col for col in print_columns if col in final_df.columns]]
    print(print_df)


    # Save results (CSV and Markdown)
    save_results(final_df, OUTPUT_FILENAME_BASE)
    generate_markdown_report(final_df, OUTPUT_FILENAME_BASE)

    logging.info("Analysis complete.")