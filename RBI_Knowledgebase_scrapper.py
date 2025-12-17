import requests
import os
import datetime
import json
import feedparser
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# --- Configuration ---

# URL for the separate metadata file
CIRCULAR_INDEX_URL = 'https://www.rbi.org.in/Scripts/BS_CircularIndexDisplay.aspx'
# URL for the real-time feed
RBI_NOTIFICATIONS_FEED = 'https://www.rbi.org.in/notifications_rss.xml' 

# Dictionary of URLs to SCRAPE FOR PDF DOWNLOADS
RBI_DOWNLOAD_URLS = {
    'Notifications': 'https://www.rbi.org.in/Scripts/NotificationUser.aspx',
    'Master_Directions': 'https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx',
    'Master_Circulars': 'https://www.rbi.org.in/Scripts/BS_ViewMasterCirculardetails.aspx',
    'Draft_Guidelines': 'https://www.rbi.org.in/Scripts/DraftNotificationsGuildelines.aspx',
    'REwise_Draft': 'https://www.rbi.org.in/Scripts/BS_ViewREwiseDraftDirections.aspx',
}

DOWNLOAD_DIR = 'rbi_full_knowledge_base'
STATUS_FILE = 'download_audit_status.json' 
INDEX_DATA_FILE = 'circular_index_data.json' 

# Global tracker for status and duplicate checks
download_status_tracker = {}

# --- Utility Functions ---

def load_status(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"[WARNING] Could not decode {file_path}. Starting fresh tracker.")
            return {}
    return {}

def save_status(data, file_path):
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)
    print(f"\n--- Data saved to {file_path} ---")

def setup_directory():
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)

def sanitize_filename(title, source_key):
    date_str = datetime.datetime.now().strftime('%Y%m%d')
    safe_title = ''.join(c if c.isalnum() or c in (' ', '_', '-') else '' for c in title)
    safe_title = safe_title.strip().replace(' ', '_')
    filename = f"{source_key}_{date_str}_{safe_title}.pdf"
    return filename[:200]

def download_file(pdf_url, filename):
    try:
        full_path = os.path.join(DOWNLOAD_DIR, filename)
        if os.path.exists(full_path):
            print(f"   [SKIP] File already exists: {filename}")
            return True

        response = requests.get(pdf_url, stream=True, timeout=30)
        response.raise_for_status()
        
        with open(full_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192): 
                f.write(chunk)
        
        print(f"   [SUCCESS] Downloaded: {filename}")
        return True

    except requests.exceptions.RequestException as e:
        print(f"   [ERROR] Failed to download {pdf_url}: {e}")
        return False
    
# ---------------------------------------------------------------------------------
# --- Core Scraping Logic (Relevant functions from v4/v5) ---
# ---------------------------------------------------------------------------------

def find_pdf_link(html_url):
    """Visits the inner HTML page and searches for the direct PDF link."""
    try:
        response = requests.get(html_url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        for link_element in soup.find_all('a', href=True):
            href = link_element['href']
            if href.lower().endswith('.pdf'):
                return urljoin(html_url, href)
            if 'title' in link_element.attrs and 'pdf' in link_element['title'].lower():
                return urljoin(html_url, href)
                
    except requests.exceptions.RequestException:
        pass
        
    return None

def process_rss_feed():
    """Monitors the RBI RSS feed and queues items for download."""
    print("\n--- Processing Real-Time RSS Feed ---")
    rss_links = []
    try:
        feed = feedparser.parse(RBI_NOTIFICATIONS_FEED)
        for entry in feed.entries:
            title = entry.get('title', 'No Title')
            link = entry.get('link', None)
            if link:
                rss_links.append({'title': title, 'link': link, 'source_key': 'RSS_Feed'})
    except Exception as e:
        print(f"--- [CRITICAL ERROR] Could not parse RSS feed: {e}")
    return rss_links

def scrape_index_page(source_key, index_url):
    """
    Scrapes index pages for PDF downloads using the class='link2' selector.
    (Logic remains the same as in v5)
    """
    print(f"\n--- Scraping Index: {source_key} ({index_url}) ---")
    document_links = []
    
    try:
        response = requests.get(index_url, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        current_classification = "UNCATEGORIZED" 
        
        link_elements = soup.find_all('a', class_='link2', href=True)
        
        for link_element in link_elements:
            link = urljoin(index_url, link_element['href'])
            title = link_element.text.strip()
            
            classification_header = link_element.find_previous('div', class_='dop_header')
            if classification_header:
                current_classification = classification_header.text.strip().replace('\n', ' ').split(':')[0].strip()
            
            if title and link:
                document_links.append({
                    'title': title, 
                    'link': link, 
                    'source_key': f"{source_key}_{current_classification}"
                })
                    
        print(f"Found {len(document_links)} potential document links on {source_key} index.")
        
    except requests.exceptions.RequestException as e:
        print(f"   [CRITICAL ERROR] Failed to fetch index page {index_url}: {e}")
        
    return document_links

# ---------------------------------------------------------------------------------
# --- MODIFIED: Circular Index Scraper ---
# ---------------------------------------------------------------------------------
# ... (rest of the script and other functions remain the same) ...

def scrape_and_save_circular_index(index_url):
    """
    Specifically scrapes the Circular Index page, maps TH headers to TD cells,
    and saves the structured metadata using known JSON keys.
    """
    print(f"\n--- Scraping Metadata: Circular Index ({index_url}) ---")
    circular_data = []
    
    # Define the desired JSON keys explicitly
    JSON_KEYS = [
        'Circular_Number', 
        'Date_Of_Issue', 
        'Department', 
        'Subject', 
        'Meant_For'
    ]

    try:
        response = requests.get(index_url, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        main_table = soup.find('table', class_='table_style') 
        if not main_table: main_table = soup.find('table')
        if not main_table:
            print("[WARNING] Could not find the main data table on the Circular Index page.")
            save_status(circular_data, INDEX_DATA_FILE)
            return []

        # 1. Get and map headers from the HTML <th> tags
        html_headers = [th.text.strip().replace('\n', ' ') for th in main_table.find('tr').find_all('th')]
        
        # Simple mapping heuristic: map the HTML header text to our desired JSON keys
        # We assume the order is consistent (S.No, Circular No, Date, Dept, Subject, Meant For)
        # We skip the S.No column if it's the first one.
        
        # We start mapping from the second TH tag (index 1) to skip the S.No column
        header_map = {}
        header_index_map = {}
        
        # The Circular Index usually has S.No as the first column, which we ignore in the JSON.
        # We map based on position starting from the second column (index 1 in TD list).
        
        # The column names found in the table will be mapped to the JSON_KEYS list
        # We will assume that the S.No column is the first one and drop it.
        # The remaining columns will be mapped sequentially to the JSON_KEYS.
        
        # Create a mapping that handles rows with the S.No column (index 0)
        final_headers = JSON_KEYS 

        # 2. Iterate over data rows
        rows = main_table.find_all('tr')[1:] 
        
        for row in rows:
            cols = row.find_all('td')
            
            # If the first column is S.No, drop it to align TD columns with JSON_KEYS
            if len(cols) == len(final_headers) + 1:
                data_cells = cols[1:] # Drop S.No column
            elif len(cols) == len(final_headers):
                data_cells = cols # Use all columns
            else:
                continue # Skip rows with unexpected column counts
            
            circular_entry = {}
            source_link = None
            
            for i, header in enumerate(final_headers):
                cell = data_cells[i]
                
                # Check for the link in the Circular Number column
                if header == 'Circular_Number':
                    link_element = cell.find('a', class_='link2', href=True)
                    
                    if link_element:
                        source_link = urljoin(index_url, link_element['href'])
                        circular_entry[header] = link_element.text.strip()
                    else:
                        circular_entry[header] = cell.text.strip()
                else:
                    # For all other columns, just extract text
                    circular_entry[header] = cell.text.strip()

            # Finalize the entry with audit/RAG parameters
            if source_link:
                circular_entry['source_html_link'] = source_link
                circular_entry['is_pdf_downloaded'] = False
                circular_entry['filename'] = None
                circular_data.append(circular_entry)

        save_status(circular_data, INDEX_DATA_FILE)
        print(f"Successfully scraped and saved {len(circular_data)} entries to {INDEX_DATA_FILE}.")
        
    except Exception as e:
        print(f"--- [CRITICAL ERROR] Error during Circular Index parsing: {e}")
            
    return circular_data
    
# ... (The rest of the script is unchanged) ...
# ... (rest of the script remains the same)

def process_all_links(all_document_links):
    """
    Iterates through all collected document links (from indices and RSS), 
    checks the audit file for prior success, avoids duplicates, and downloads only 
    new or failed records.
    """
    unique_links = set()
    
    # 1. Start with the current total count of documents to process
    print(f"\n--- Starting Processing of {len(all_document_links)} Total Links ---")
    
    skipped_count = 0
    download_queue = []
    
    for doc in all_document_links:
        link = doc['link']
        
        # --- PRIMARY AUDIT CHECK ---
        # 1a. Check if the link key exists in the tracker AND was successfully downloaded
        if link in download_status_tracker and download_status_tracker[link]['downloaded']:
            skipped_count += 1
            continue

        # 1b. Check for duplicate links within the current run
        if link in unique_links:
            continue
            
        unique_links.add(link)
        download_queue.append(doc)

    print(f"Skipped {skipped_count} documents already marked as successful in {STATUS_FILE}.")
    print(f"Processing {len(download_queue)} new or failed documents.")

    # 2. Process the reduced queue
    for doc in download_queue:
        link = doc['link']
        title = doc['title']
        source_key = doc['source_key']
        
        print(f"\n[PROCESSING] {source_key} - {title[:80]}...")
        
        pdf_url = None
        
        # Determine the final PDF URL
        if link.lower().endswith('.pdf'):
            pdf_url = link
        else:
            pdf_url = find_pdf_link(link)
        
        success = False
        filename = None
        
        if pdf_url:
            filename = sanitize_filename(title, source_key)
            
            # --- SECONDARY FILE CHECK ---
            # Check if the file already exists on disk (an extra layer of protection)
            if os.path.exists(os.path.join(DOWNLOAD_DIR, filename)):
                print(f"   [SKIP] File already exists on disk: {filename}")
                success = True # Mark as successful since the file is present
            else:
                success = download_file(pdf_url, filename)
        else:
            print("   [FAILED] Could not find a downloadable PDF link.")
        
        # 3. Update Status Tracker with the final result
        download_status_tracker[link] = {
            'title': title, 
            'source': source_key,
            'downloaded': success, 
            'filename': filename if success else None,
            'final_pdf_url': pdf_url,
            'source_html_link': link
        }
    
    # The rest of the main function handles saving the status and summary
def main():
    """Orchestrates the scraping process."""
    global download_status_tracker
    download_status_tracker = load_status(STATUS_FILE)
    setup_directory()
    
    # STEP 1: Process the Circular Index Metadata (Separate Structured JSON)
    scrape_and_save_circular_index(CIRCULAR_INDEX_URL)

    # STEP 2: Scrape all PDF Index pages (HTML tables)
    all_document_links = []
    for source_key, url in RBI_DOWNLOAD_URLS.items():
        links = scrape_index_page(source_key, url)
        all_document_links.extend(links)
        
    # STEP 3: Process the RSS Feed (Real-Time XML feed)
    rss_links = process_rss_feed()
    all_document_links.extend(rss_links)
        
    # STEP 4: Process and download all unique document links
    process_all_links(all_document_links)

    # STEP 5: Save the final audit status
    save_status(download_status_tracker, STATUS_FILE)
    
    # 6. Final summary
    downloaded_count = sum(1 for status in download_status_tracker.values() if status['downloaded'])
    failed_count = sum(1 for status in download_status_tracker.values() if not status['downloaded'])
    print(f"\n===========================================")
    print(f"| Ingestion Summary:")
    print(f"| Total Unique Documents Tracked: {len(download_status_tracker)}")
    print(f"| Successfully Downloaded: {downloaded_count}")
    print(f"| Failed Downloads: {failed_count}")
    print(f"| Metadata Saved to: {INDEX_DATA_FILE}")
    print(f"| Audit Saved to: {STATUS_FILE}")
    print(f"===========================================")
    
if __name__ == '__main__':
    main()