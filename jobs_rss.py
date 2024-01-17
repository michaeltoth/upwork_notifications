import feedparser
import os
import pytz
import re
import requests
import sys
import time
import urllib.parse
from datetime import datetime

# To edit your searches, use this Google sheet: 
# https://docs.google.com/spreadsheets/d/192sTJjDw1tHFHD-0O9ll545rXVFdRc_TM6qPlCAo0pQ/edit#gid=1969734536

# This system is based on the logic laid out in this Youtube video:
# https://www.youtube.com/watch?v=PVjk8NJljGM


base_url = "https://www.upwork.com/ab/feed/jobs/rss"
categories = "?category2_uid=531770282580668420%2C531770282580668422%2C531770282580668418%2C531770282580668423"
slack_token = os.getenv('SLACK_TOKEN')
print(slack_token)



def safe_print(content):
    try:
        sys.stdout.write(content + '\n')
    except UnicodeEncodeError:
        # Silently handle the error
        pass



def convert_to_query(search_phrase):
    # Encoding the search phrase for URL
    encoded_phrase = urllib.parse.quote_plus(search_phrase)
    query = "&q=" + encoded_phrase
    return query


def extract_job_details(summary):
    details = {}

    # Word count in the summary
    word_count = len(summary.split())
    print(word_count)

    # Extracting Budget
    budget_match = re.search(r'<b>Budget</b>: \$(\d+)', summary)
    if budget_match:
        budget = int(budget_match.group(1))
        if budget < 500:
            print(str(budget) + " BELOW $500, REMOVING")
            return None  # Filter out jobs with budget less than $500
        details['Budget'] = f"${budget}"

    # Extracting Hourly Range
    hourly_range_match = re.search(r'<b>Hourly Range</b>: \$\d+\.\d{2}-\$(\d+\.\d{2})', summary)
    if hourly_range_match:
        hourly_top_end = float(hourly_range_match.group(1))
        if hourly_top_end < 80:
            print(str(hourly_top_end) + " BELOW $80, REMOVING")
            return None  # Filter out jobs with top end of hourly range less than $80
        details['Hourly Range'] = f"Up to ${hourly_top_end}"

    # Extracting Category
    category_match = re.search(r'<b>Category</b>: ([\w\s]+)<br', summary)
    if category_match:
        details['Category'] = category_match.group(1).strip()

    # Extracting Skills
    skills_match = re.search(r'<b>Skills</b>:(.+?)<br', summary)
    if skills_match:
        skills = skills_match.group(1).strip()
        details['Skills'] = [skill.strip() for skill in skills.split(',') if skill.strip()]

    return details if details else None


def process_item(item, channel_id='C06AJB2LGAC'):
    # Your logic to process the item
    print(f"Processing item: {item.title.encode('utf-8')}")

    details = extract_job_details(item.summary)
    
    if details is None:
        return

    slack_url = "https://slack.com/api/chat.postMessage"
    
    headers = {
        "Authorization": f"Bearer {slack_token}",
        "Content-Type": "application/json; charset=utf-8"
    }


    # Convert and format the published time
    try:
        published_dt = datetime.strptime(item.published, '%a, %d %b %Y %H:%M:%S %z')
        mountain_tz = pytz.timezone('America/Denver')
        published_mountain = published_dt.astimezone(mountain_tz).strftime('%a, %d %b %Y %I:%M %p')
    except ValueError as e:
        print(f"Error parsing date: {e}")
        published_mountain = item.published  # fallback to original date string


    # Replacing <br> and <br /> tags with newline characters
    formatted_summary = item.summary.replace('<br>', '\n').replace('<br />', '\n')

    # Regular expressions for removing specific sections and link
    # This removes them from the "summary" that gets posted
    sections_to_remove = ['Posted On', 'Category', 'Skills', 'Country', 'Hourly Range', 'Location Requirement', 'Budget']
    for section in sections_to_remove:
        formatted_summary = re.sub(r'<b>' + re.escape(section) + r'</b>:.*?(\n|$)', '', formatted_summary, flags=re.DOTALL)

    # Removing the link
    formatted_summary = re.sub(r'<a href=".*?">.*?</a>', '', formatted_summary, flags=re.DOTALL)

    # Truncating to the first 500 words
    #summary_words = formatted_summary.split()[:200]
    #short_summary = ' '.join(summary_words)
    #print(short_summary)

    # Constructing the message with block formatting
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{item.title}*\nPublished: {published_mountain}"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Search Phrase: {item.search_phrase}"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": formatted_summary
            }
        }
    ]


    # Duplicate of the above
    # blocks.insert(1, {
    #     "type": "section",
    #     "text": {
    #         "type": "mrkdwn",
    #         "text": f"Search Phrase: {item.search_phrase}"
    #     }
    # })

    # Adding additional fields if available
    fields = []
    if 'Hourly Range' in details:
        fields.append({"type": "mrkdwn", "text": f"*Hourly Range:* {details['Hourly Range']}"})
    elif 'Budget' in details:
        fields.append({"type": "mrkdwn", "text": f"*Budget:* {details['Budget']}"})
    if 'Skills' in details:
        skills_formatted = ', '.join(details['Skills'])
        fields.append({"type": "mrkdwn", "text": f"*Skills:* {skills_formatted}"})

    if fields:
        blocks.append({"type": "section", "fields": fields})

    # Adding the link to the job
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"<{item.link}|Link to Job>"
        }
    })

    payload = {
        "channel": channel_id,
        "blocks": blocks
    }

    response = requests.post(slack_url, headers=headers, json=payload)

    if response.status_code != 200:
        print(f"Error sending message to Slack: {response.text}")
    else:
        print("Message sent to Slack successfully")


def is_processed(item_id, processed_jobs):
    return item_id in processed_jobs


def get_processed_jobs(file_path):
    try:
        with open(file_path, 'r') as file:
            return set(file.read().splitlines())
    except FileNotFoundError:
        return set()


def add_processed_id(item_id, file_path):
    with open(file_path, 'a') as file:
        file.write(item_id + '\n')


def get_rss(search_phrase):
    # Convert to RSS-readable URL format
    query = convert_to_query(search_phrase)
    url = base_url + categories + query
    
    feed = feedparser.parse(url)

    for entry in feed.entries:
        entry.search_phrase = search_phrase  # Add the search phrase to each entry

    num_jobs = len(feed.entries)
    print(num_jobs)

    return(feed.entries)



# def create_search_phrases(positive_keywords, negative_keywords):
# 	phrase = ""
# 	phrase_len = len(phrase)

# 	# Create negative phrase
# 	if len(negative_keywords) > 0:
# 		negative_phrase = "AND NOT "

# 		for keyword in negative_keywords:
# 			negative_phrase = negative_phrase + '"' + keyword + '"'
# 	negative_phrase = 

# 	while phrase_len < 500:




def main():
    processed_jobs_file = 'processed_jobs.txt'

    # negative_keywords = "Youtube Automation"
    # positive_keywords = ["zapier development","zapier programmer","zapier developer","zapier professional","zapier rockstar","zapier pro","zapier consultant","zapier specialist","zapier expert","automation integration","automation development","automation programmer","automation developer","automation professional","automation rockstar","automation pro","automation consultant","automation specialist","automation expert","integromat programmer","integromat developer","integromat professional","integromat rockstar","integromat pro","integromat consultant","integromat specialist","integromat expert","make.com integration","make.com development","make.com programmer","make.com developer","make.com professional","make.com rockstar","make.com pro","make.com consultant","make.com specialist","make.com expert","zapier integration","API development","API programmer","API developer","API professional","API rockstar","API pro","API consultant","API specialist","API expert","Airtable integration","Airtable development","Airtable programmer","Airtable developer","Airtable professional","Airtable rockstar","Airtable pro","Airtable consultant","Airtable specialist","Airtable expert","integromat integration","integromat development","ChatGPT consultant","ChatGPT specialist","ChatGPT expert","GPT integration","GPT development","GPT programmer","GPT developer","GPT professional","GPT rockstar","GPT pro","GPT consultant","GPT specialist","GPT expert","AI integration","AI development","AI programmer","AI developer","AI professional","AI rockstar","AI pro","AI consultant","AI specialist","AI expert","API integration","using ChatGPT","with ChatGPT","using GPT","with GPT","using AI","with AI","using API","with API","using Airtable","with Airtable","using integromat","with integromat","using make.com","with make.com","using zapier","with zapier","ChatGPT integration","ChatGPT development","ChatGPT programmer","ChatGPT developer","ChatGPT professional","ChatGPT rockstar","ChatGPT pro"]

    # create_search_phrases(positive_keywords, negative_keywords)

    search_phrase_1 = '''("zapier development" OR "zapier programmer" OR "zapier developer" OR "zapier professional" OR "zapier rockstar" OR "zapier pro" OR "zapier consultant" OR "zapier specialist" OR "zapier expert" OR "automation integration" OR "automation development" OR "automation programmer" OR "automation developer" OR "automation professional" OR "automation rockstar" OR "automation pro" OR "automation consultant" OR "automation specialist" OR "automation expert") AND NOT (Youtube Automation)'''
    search_phrase_2 = '''("integromat programmer" OR "integromat developer" OR "integromat professional" OR "integromat rockstar" OR "integromat pro" OR "integromat consultant" OR "integromat specialist" OR "integromat expert" OR "make.com integration" OR "make.com development" OR "make.com programmer" OR "make.com developer" OR "make.com professional" OR "make.com rockstar" OR "make.com pro" OR "make.com consultant" OR "make.com specialist" OR "make.com expert" OR "zapier integration") AND NOT (Youtube Automation)'''
    search_phrase_3 = '''("API development" OR "API programmer" OR "API developer" OR "API professional" OR "API rockstar" OR "API pro" OR "API consultant" OR "API specialist" OR "API expert" OR "Airtable integration" OR "Airtable development" OR "Airtable programmer" OR "Airtable developer" OR "Airtable professional" OR "Airtable rockstar" OR "Airtable pro" OR "Airtable consultant" OR "Airtable specialist" OR "Airtable expert" OR "integromat integration" OR "integromat development") AND NOT (Youtube Automation)'''
    search_phrase_4 = '''("ChatGPT consultant" OR "ChatGPT specialist" OR "ChatGPT expert" OR "GPT integration" OR "GPT development" OR "GPT programmer" OR "GPT developer" OR "GPT professional" OR "GPT rockstar" OR "GPT pro" OR "GPT consultant" OR "GPT specialist" OR "GPT expert" OR "AI integration" OR "AI development" OR "AI programmer" OR "AI developer" OR "AI professional" OR "AI rockstar" OR "AI pro" OR "AI consultant" OR "AI specialist" OR "AI expert" OR "API integration") AND NOT (Youtube Automation)'''
    search_phrase_5 = '''("using ChatGPT" OR "with ChatGPT" OR "using GPT" OR "with GPT" OR "using AI" OR "with AI" OR "using API" OR "with API" OR "using Airtable" OR "with Airtable" OR "using integromat" OR "with integromat" OR "using make.com" OR "with make.com" OR "using zapier" OR "with zapier" OR "ChatGPT integration" OR "ChatGPT development" OR "ChatGPT programmer" OR "ChatGPT developer" OR "ChatGPT professional" OR "ChatGPT rockstar" OR "ChatGPT pro") AND NOT (Youtube Automation)'''


    # Negative keywords to consider:
    # Full stack
    # Fullstack
    # Backend developer
    # 

    search_phrases = [search_phrase_1, search_phrase_2, search_phrase_3, search_phrase_4, search_phrase_5]

    combined_entries = []
    for search_phrase in search_phrases:
        entries = get_rss(search_phrase)
        combined_entries.extend(entries)

    
    # Load processed jobs from file
    processed_jobs = get_processed_jobs(processed_jobs_file)

    # Initialize a set for runtime processed jobs
    runtime_processed_jobs = set()

    for entry in combined_entries:
        item_id = entry.id

        # Prior solution that did not handle duplicates across search phrases:
        # if not is_processed(item_id, processed_jobs):
        #     process_item(entry)
        #     add_processed_id(item_id, processed_jobs_file)
        # else:
        #     print(f"Skipping already processed item: {entry.title}")

    	# Check against both file-based and runtime processed jobs:
        if item_id not in processed_jobs and item_id not in runtime_processed_jobs:
            process_item(entry)
            add_processed_id(item_id, processed_jobs_file) # Update the processed jobs file
            runtime_processed_jobs.add(item_id)  # Update the runtime set
        else:
            safe_print(f"Skipping already processed item: {entry.title}")

if __name__ == "__main__":
    main()




# TODO: Integrate ChatGPT for automatically generating a shell response
# TODO: Filter out descriptions that are too short / too long
# TODO: Eliminate last-minute/urgent jobs
# TODO: Eliminate non-English job postings
# TODO: Is it possible to create some type of ChatGPT system that learns more accurate suggestions over time?