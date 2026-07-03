def search_and_scrape(query):
    # 從環境變數讀取官方 API 金鑰
    api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
    cx = os.environ.get("SEARCH_ENGINE_ID")
    
    if not api_key or not cx:
        return "系統設定錯誤：缺少 Google 搜尋 API 金鑰。"

    # 呼叫官方 API（設定 num=5 抓取前五筆結果）
    search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={api_key}&cx={cx}&num=5"
    
    try:
        res = requests.get(search_url, timeout=10)
        data = res.json()
        
        if 'items' not in data:
            return "官方 API 搜尋不到相關網頁結果，或您的每日免費額度(100次)已用盡。"
            
        combined_text = ""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        for item in data['items']:
            link = item.get('link')
            snippet = item.get('snippet', '無摘要') # 官方提供的搜尋預覽文字
            
            # 嘗試進一步爬取點進去後的目標網頁內文
            page_text = ""
            try:
                page_res = requests.get(link, headers=headers, timeout=5)
                page_soup = BeautifulSoup(page_res.text, 'html.parser')
                paragraphs = page_soup.find_all('p')
                page_text = " ".join([p.get_text() for p in paragraphs[:5]])
            except:
                page_text = "無法讀取此網頁完整內文（該目標網站可能設有獨立的反爬蟲阻擋）。"
                
            # 將官方摘要與網頁內文組合，提供給 Gemini 分析
            combined_text += f"\n[來源網頁: {link}]\nGoogle官方摘要: {snippet}\n網頁擷取內文: {page_text[:300]}\n"
            
        return combined_text
        
    except Exception as e:
        return f"搜尋系統發生錯誤: {str(e)}"
