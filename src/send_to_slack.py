from datetime import datetime
import requests

def _log(log_fn, message):
    if log_fn:
        log_fn(message)
    else:
        print(message)


def get_top_papers(results, top_n=10, log_fn=None):
    """Filter to only relevant papers and get top N by score"""
    
    # Filter to relevant only
    relevant = [r for r in results if r.get('is_relevant', False)]
    
    _log(log_fn, f"Found {len(relevant)} relevant papers out of {len(results)} total")
    
    # Sort by relevance_score descending
    relevant.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
    
    # Return top N
    top_papers = relevant[:top_n]
    
    _log(log_fn, f"Returning top {len(top_papers)} papers")
    for i, p in enumerate(top_papers, 1):
        _log(log_fn, f"  {i}. [{p['relevance_score']}/10] {p['paper_id']}")
    
    return top_papers

def send_to_slack(top_results, papers, webhook_url):
    """Send top 10 to Slack"""
    
    if not top_results:
        print("No relevant papers to send")
        return
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📚 Team Research Digest -  {datetime.now().strftime('%B %d, %Y')}"}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Found {len(top_results)} papers out of {len(papers)} new papers today worth reading based on your team's focus."}
            }, 
        {"type": "divider"}
    ]
    
    for i, result in enumerate(top_results, 1):
        # Find the paper
        paper = next((p for p in papers if p['id'] == result['paper_id']), None)
        if not paper:
            continue
        
        summary_text = result.get('summary') if result.get('needs_summary') else paper['abstract'][:200] + "..."
        
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{i}. <{paper['url']}|{paper['title']}>*\n"
                        f"⭐ Score: {result['relevance_score']}/10\n"
                        f"💡 {result['key_insight']}\n\n"
                        f"_{summary_text}_"
            }
        })
        blocks.append({"type": "divider"})
    
    response = requests.post(
        webhook_url,
        json={"blocks": blocks},
        headers={'Content-Type': 'application/json'}
    )
    
    if response.status_code == 200:
        print(f"✓ Sent {len(top_results)} papers to Slack!")
    else:
        print(f"✗ Failed: {response.status_code}")
