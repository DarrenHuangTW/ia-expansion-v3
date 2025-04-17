import os
import requests
import pandas as pd
from firecrawl import FirecrawlApp
from pydantic import BaseModel
from typing import Optional, List, Dict, Tuple, Any
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables once
load_dotenv()
SERPAPI_API_KEY = os.getenv('serpapi_api_key')
FIRECRAWL_API_KEY = os.getenv('firecrawl_api_key')

# Helper function for Firecrawl calls to reduce repetition
def _call_firecrawl_extract(url: str, prompt: str, schema: Optional[Dict] = None) -> Optional[Dict]:
    """Helper function to call Firecrawl extract API."""
    if not FIRECRAWL_API_KEY:
        logging.error("Firecrawl API key not found.")
        return None
    try:
        app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
        params = {'prompt': prompt}
        if schema:
            params['schema'] = schema

        logging.info(f"Calling Firecrawl for URL: {url}")
        # Make the API call (assuming extract takes a list of URLs)
        response = app.extract([url], params=params) 

        # Check response structure (can be dict for single URL or list for multiple)
        response_data = None
        if response and isinstance(response, list) and len(response) > 0:
            response_data = response[0] # Handle list case (get first item)
        elif response and isinstance(response, dict):
             response_data = response # Handle dictionary case directly

        if response_data:
            if response_data.get('error'):
                 logging.error(f"Error from Firecrawl for URL {url}: {response_data['error']}")
                 return None
            # Check if 'data' exists and is not None before returning
            data = response_data.get('data')
            if data:
                 logging.info(f"Firecrawl success for URL: {url}")
                 return data
            else:
                 logging.warning(f"Firecrawl returned no 'data' for URL {url}. Response: {response_data}")
                 return None
        else:
             # Log the original response if it wasn't a recognized format
            logging.warning(f"Unexpected or empty Firecrawl response format for URL {url}. Response: {response}")
            return None
    except Exception as e:
        logging.error(f"Error during Firecrawl API call for URL {url}: {e}", exc_info=True)
        return None


def get_organic_results(keyword: str, site_path: str) -> Tuple[Optional[str], pd.DataFrame, Optional[str]]:
    """
    Retrieves organic Google search results for a keyword restricted to a specific site using SerpApi.

    Args:
        keyword: The search keyword.
        site_path: The domain/path to restrict the search (e.g., 'bloomsthechemist.com.au').

    Returns:
        A tuple containing:
        - The SerpApi raw HTML file URL (or None).
        - A DataFrame with columns ['Position', 'Ranking URL', 'Snippet'] (or empty).
        - The SerpApi raw HTML file URL again (for consistency, though redundant in tuple). Will adjust caller.
    """
    if not SERPAPI_API_KEY:
        logging.error("SerpApi API key not found.")
        return None, pd.DataFrame(columns=['Position', 'Ranking URL', 'Snippet']), None

    # Ensure site_path is just the domain or domain/path without protocol
    site_path_cleaned = site_path.replace('https://', '').replace('http://', '').strip('/')
    # Replace '&' with 'and' in the keyword for the query
    query_keyword = keyword.replace('&', 'and')
    q = f"{query_keyword} site:{site_path_cleaned}"
    api_url = f'https://serpapi.com/search.json?engine=google&api_key={SERPAPI_API_KEY}&q={q}&num=10'

    logging.info(f"Calling SerpApi for keyword: '{keyword}', site: '{site_path_cleaned}'")
    try:
        response = requests.get(api_url, timeout=30) # Add timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        data = response.json()
        search_results = data.get('organic_results', [])
        raw_html_file = data.get('search_metadata', {}).get('raw_html_file')

        ranking_data = [
            (index + 1, result.get('link'), result.get('snippet'))
            for index, result in enumerate(search_results)
            if result.get('link') # Ensure there's a link
        ]
        result_df = pd.DataFrame(ranking_data, columns=['Position', 'Ranking URL', 'Snippet'])
        logging.info(f"SerpApi success for keyword: '{keyword}'. Found {len(result_df)} results.")
        return raw_html_file, result_df # Return raw_html_file first as per original logic

    except requests.exceptions.RequestException as e:
        logging.error(f"Error during SerpApi request for keyword '{keyword}': {e}")
        return None, pd.DataFrame(columns=['Position', 'Ranking URL', 'Snippet']), None
    except Exception as e:
        logging.error(f"An unexpected error occurred in get_organic_results for keyword '{keyword}': {e}", exc_info=True)
        return None, pd.DataFrame(columns=['Position', 'Ranking URL', 'Snippet']), None


def assess_category_page_relevance(keyword: str, url: str) -> Optional[Dict]:
    """
    Assesses the relevance of an e-commerce category page URL to a given keyword using Firecrawl.

    Args:
        keyword: The keyword/product type/topic to check relevance against.
        url: The URL of the category page.

    Returns:
        A dictionary with 'Relevant' and 'Analysis' keys, or None on error/failure.
        Example: {'Relevant': 'Closely Related', 'Analysis': '...'}
    """
    class ExtractSchema(BaseModel):
        Relevant: str # Expecting "Closely Related", "Loosely Related", or "Unrelated"
        Analysis: str

    prompt = f'''Evaluate the specificity and focus of the provided e-commerce category page ({url}) in relation to the specific product type or topic "{keyword}".

Determine the degree of relevance based on these definitions:
- "Closely Related": The page is *primarily and specifically* dedicated to "{keyword}" products. Most products listed directly match "{keyword}", and the page title/breadcrumbs reflect this specific focus.
- "Loosely Related": The page *includes* products matching "{keyword}", but it represents a broader category containing a significant number of other, less directly related product types. The page title/breadcrumbs likely indicate this broader scope (e.g., a general 'First Aid' page containing burn items). Provide examples.
- "Unrelated": The page does not feature products matching "{keyword}".

Respond ONLY with the following JSON format:
{{"Relevant": "(Closely Related, Loosely Related, Unrelated)", "Analysis": "(Provide a concise explanation justifying your choice based on the definitions above. If 'Closely Related', confirm the page's specific focus on '{keyword}'. If 'Loosely Related', explain how it's a broader category that includes '{keyword}' alongside other product types, mentioning the page's apparent scope. If 'Unrelated', state that '{keyword}' products are absent.)"}}'''

    logging.info(f"Assessing category relevance for keyword '{keyword}' on URL: {url}")
    result = _call_firecrawl_extract(url, prompt, ExtractSchema.model_json_schema())
    if result and isinstance(result, dict) and 'Relevant' in result and 'Analysis' in result:
        return result
    else:
        logging.warning(f"Failed to assess category relevance or invalid format for keyword '{keyword}', URL '{url}'. Result: {result}")
        return None # Return None if validation fails


def assess_product_page_relevance(keyword: str, url: str) -> Optional[Dict]:
    """
    Assesses the relevance of a landing page URL (likely a PDP or other content page)
    to a given keyword using Firecrawl.

    Args:
        keyword: The keyword/product type/topic to check relevance against.
        url: The URL of the landing page.

    Returns:
        A dictionary with 'Relevance' and 'Analysis' keys, or None on error/failure.
        Example: {'Relevance': 'Related', 'Analysis': '...'}
    """
    class ExtractSchema(BaseModel):
        Relevance: str # Expecting "Related" or "Unrelated"
        Analysis: str

    # Refined prompt based on function's goal
    prompt = f'''Evaluate the provided landing page ({url}) for its relevance to the specific product type or topic "{keyword}". Determine if the page content directly matches user expectations for "{keyword}" by either being the specific product type or by including "{keyword}" as a feature, component, or bundled item. Respond ONLY with JSON in the following format: {{"Relevance": "(Related/Unrelated)", "Analysis": "(Provide a brief explanation. If Related, explain why the page precisely matches the product type/topic \'{keyword}\' or how it includes \'{keyword}\' as a feature or component. If Unrelated, explain why it fails to do so, focusing on the mismatch between the query \'{keyword}\' and the content/products offered on the page.)"}}'''

    logging.info(f"Assessing product/page relevance for keyword '{keyword}' on URL: {url}")
    result = _call_firecrawl_extract(url, prompt, ExtractSchema.model_json_schema())
    if result and isinstance(result, dict) and 'Relevance' in result and 'Analysis' in result:
        return result
    else:
        logging.warning(f"Failed to assess product/page relevance or invalid format for keyword '{keyword}', URL '{url}'. Result: {result}")
        return None # Return None if validation fails


def classify_and_assess_url(keyword: str, url: str) -> Optional[Dict]:
    """
    Uses Firecrawl to classify a URL's page type (PLP, PDP, Other) AND assess its relevance
    to the keyword in a single call.

    Args:
        keyword: The keyword/topic to check relevance against.
        url: The URL of the page to analyze.

    Returns:
        A dictionary with 'determined_type', 'relevance', and 'analysis' keys, or None on error.
        Example: {'determined_type': 'PLP', 'relevance': 'Loosely Related', 'analysis': '...'}
                 {'determined_type': 'PDP', 'relevance': 'Related', 'analysis': '...'}
                 {'determined_type': 'Other', 'relevance': 'N/A', 'analysis': 'Page is informational.'}
    """
    class CombinedSchema(BaseModel):
        determined_type: str # Expecting "PLP", "PDP", "Brand Page", "Article", "Other"
        relevance: str       # Expecting "Closely Related", "Loosely Related", "Unrelated", "Related", "N/A"
        analysis: str        # Explanation

    # Combined prompt asking for type classification and then relevance based on that type
    prompt = f'''Analyze the content of the page at {url}. First, determine its primary type. Choose ONE from: "PLP" (Product Listing Page/Category Page), "PDP" (Product Detail Page), "Brand Page" (Page dedicated to a specific brand), "Article" (Blog post or informational content), "Other" (Homepage, contact, help, etc.).

Second, evaluate the page's relevance to the keyword/topic "{keyword}".
- If determined_type is "PLP" or "Brand Page", assess relevance as "Closely Related", "Loosely Related", or "Unrelated" based on how well the page's product selection or focus matches "{keyword}". Provide analysis.
- If determined_type is "PDP", assess relevance strictly based on whether the product *is* the specific type "{keyword}". Use "Related" ONLY if the product is clearly identifiable as "{keyword}" (e.g., the product title or description explicitly states it's "{keyword}" or an extremely close synonym/variant, like 'Hair Styling Powder' for 'Hair Powder'). Do NOT consider products that merely serve a similar purpose or belong to the same general category as "Related". If it's not the specific product type, use "Unrelated". Provide analysis explaining the match or mismatch of the *product type* itself.
- If determined_type is "Article" or "Other", set relevance to "N/A" and provide a brief analysis of the page's content.

Respond ONLY with JSON in the following format:
{{"determined_type": "(PLP/PDP/Brand Page/Article/Other)", "relevance": "(Closely Related/Loosely Related/Unrelated/Related/N/A)", "analysis": "(Your analysis here)"}}'''

    logging.info(f"Classifying and assessing URL '{url}' for keyword '{keyword}'")
    result = _call_firecrawl_extract(url, prompt, CombinedSchema.model_json_schema())

    # Validate the structure of the returned data
    if (result and isinstance(result, dict) and
            'determined_type' in result and
            'relevance' in result and
            'analysis' in result):
        # Basic validation of expected values (can be expanded)
        allowed_types = ["PLP", "PDP", "Brand Page", "Article", "Other"]
        allowed_relevance = ["Closely Related", "Loosely Related", "Unrelated", "Related", "N/A"]
        if result['determined_type'] in allowed_types and result['relevance'] in allowed_relevance:
             return result
        else:
            logging.warning(f"Invalid values in classification/assessment for keyword '{keyword}', URL '{url}'. Result: {result}")
            return None
    else:
        logging.warning(f"Failed classification/assessment or invalid format for keyword '{keyword}', URL '{url}'. Result: {result}")
        return None