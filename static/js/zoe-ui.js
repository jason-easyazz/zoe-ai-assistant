const ZoeUI = {
  state: { isListening: false, backend: false, currentPanel: 'dashboardPanel' },
  init() {
    this.cache();
    this.bind();
    this.updateTime();
    setInterval(() => this.updateTime(), 1000);
    this.updateWeather();
    setInterval(() => this.updateWeather(), 5 * 60 * 1000);
    this.checkBackend();
    setInterval(() => this.checkBackend(), 30000);
    this.calendar.build();
    this.loadTasks();
    this.loadEvents();
    this.restorePanel();
  },
  cache() {
    this.orb = document.getElementById('orbMode');
    this.nav = document.getElementById('topNav');
    this.navItems = Array.from(this.nav.querySelectorAll('li'));
    this.panels = Array.from(document.querySelectorAll('.panel'));
    this.toastEl = document.getElementById('toast');
    this.chatOverlay = document.getElementById('chatOverlay');
    this.chatMessages = document.getElementById('chatMessages');
    this.chatInput = document.getElementById('chatInput');
    this.taskList = document.getElementById('taskList');
    this.tasksAll = document.getElementById('tasksAll');
    this.eventList = document.getElementById('eventList');
    this.dayEvents = document.getElementById('dayEvents');
    this.shoppingList = document.getElementById('shoppingList');
  },
  bind() {
    if (this.orb) this.orb.addEventListener('click', () => this.enterInterface());
    document.getElementById('navOrb').addEventListener('click', () => this.exitToOrb());
    this.navItems.forEach(li => li.addEventListener('click', () => this.switchPanel(li.dataset.panel)));
    document.addEventListener('keydown', e => {
      if (e.code === 'Space' && this.orb && !this.nav.classList.contains('active')) {
        e.preventDefault();
        this.enterInterface();
      }
      if (e.code === 'Escape') {
        if (!this.chatOverlay.classList.contains('hidden')) this.closeChatOverlay();
        else this.exitToOrb();
      }
    });
    // quick actions
    document.querySelectorAll('.quick-actions button').forEach(btn => btn.addEventListener('click', () => this.quickAction(btn.dataset.action)));
    // tasks
    this.taskList.addEventListener('click', e => { if (e.target.matches('li')) this.toggleTask(e.target); });
    this.tasksAll.addEventListener('click', e => { if (e.target.matches('li')) this.toggleTask(e.target); });
    // shopping
    document.getElementById('shoppingForm').addEventListener('submit', e => { e.preventDefault(); this.addShoppingItem(); });
    this.shoppingList.addEventListener('click', e => {
      if (e.target.matches('input[type="checkbox"]')) this.toggleShoppingItem(e.target.closest('li'));
      if (e.target.matches('button.delete')) e.target.closest('li').remove();
    });
    // chat overlay
    document.getElementById('chatForm').addEventListener('submit', e => { e.preventDefault(); this.sendMessageOverlay(); });
    document.getElementById('voiceBtn').addEventListener('click', () => this.toggleVoiceOverlay());
    // calendar controls
    document.getElementById('calPrev').addEventListener('click', () => this.calendar.prev());
    document.getElementById('calNext').addEventListener('click', () => this.calendar.next());
    document.getElementById('addEventBtn').addEventListener('click', () => this.openChatOverlay());
  },
  enterInterface() {
    this.orb.classList.add('hidden');
    this.nav.classList.remove('hidden');
    this.nav.classList.add('active');
    this.switchPanel(this.state.currentPanel);
  },
  exitToOrb() {
    this.nav.classList.add('hidden');
    this.nav.classList.remove('active');
    this.panels.forEach(p => p.classList.add('hidden'));
    this.orb.classList.remove('hidden');
    this.toast('Orb mode');
  },
  toggleVoiceFromOrb() {
    if (!this.state.isListening) this.startVoice(); else this.stopVoice();
  },
  startVoice() {
    this.state.isListening = true;
    this.orb.classList.remove('idle');
    this.orb.classList.add('listening');
    if (this.state.backend) fetch('/api/voice/start', {method:'POST'}).catch(()=>{});
  },
  stopVoice() {
    this.state.isListening = false;
    this.orb.classList.remove('listening', 'speaking');
    this.orb.classList.add('idle');
    if (this.state.backend) fetch('/api/voice/stop', {method:'POST'}).catch(()=>{});
  },
  switchPanel(id) {
    this.panels.forEach(p => p.classList.add('hidden')); 
    const panel = document.getElementById(id);
    if (panel) panel.classList.remove('hidden');
    this.navItems.forEach(li => li.classList.toggle('active', li.dataset.panel === id));
    this.state.currentPanel = id;
    localStorage.setItem('lastPanel', id);
    this.toast(`Switched to ${id.replace('Panel','')}`);
  },
  restorePanel() {
    const last = localStorage.getItem('lastPanel');
    if (last) this.state.currentPanel = last;
  },
  updateTime() {
    const now = new Date();
    document.getElementById('timeDisplay').textContent = now.toLocaleTimeString();
  },
  async updateWeather() {
    try {
      const r = await fetch('/api/weather');
      if (!r.ok) throw new Error();
      const data = await r.json();
      document.getElementById('weatherDisplay').textContent = `${data.temp || data.temperature}° ${data.condition || ''}`;
    } catch {
      this.setDefaultWeather();
    }
  },
  setDefaultWeather() {
    document.getElementById('weatherDisplay').textContent = '72° Partly Cloudy';
  },
  async checkBackend() {
    try {
      const r = await fetch('/api/health');
      this.state.backend = r.ok;
    } catch {
      this.state.backend = false;
    }
  },
  openChatOverlay() {
    this.chatOverlay.classList.remove('hidden');
    this.chatInput.focus();
  },
  closeChatOverlay() {
    this.chatOverlay.classList.add('hidden');
  },
  toggleVoiceOverlay() {
    this.state.isListening = !this.state.isListening;
    this.toast(this.state.isListening ? 'Listening...' : 'Stopped');
    if (this.state.backend) fetch(`/api/voice/${this.state.isListening?'start':'stop'}`, {method:'POST'}).catch(()=>{});
  },
  async sendMessageOverlay() {
    const msg = this.chatInput.value.trim();
    if (!msg) return;
    this.addMessage('user', msg);
    this.chatInput.value = '';
    if (this.state.backend) {
      try {
        const r = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:msg})});
        const data = await r.json();
        this.addMessage('zoe', data.reply || JSON.stringify(data));
      } catch {
        this.addMessage('zoe', 'Error contacting backend.');
      }
    } else {
      this.typingOn();
      setTimeout(() => {
        this.typingOff();
        this.addMessage('zoe', 'This is a demo reply.');
      }, 1000);
    }
  },
  addMessage(sender, text) {
    const div = document.createElement('div');
    div.className = sender === 'zoe' ? 'zoe-message' : 'user-message';
    div.textContent = text;
    this.chatMessages.appendChild(div);
    this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
  },
  typingOn() {
    this.addMessage('zoe', '...');
  },
  typingOff() {
    const last = this.chatMessages.lastElementChild;
    if (last && last.textContent === '...') last.remove();
  },
  quickAction(type) {
    const prompts = {
      journal: 'Create a journal entry about...',
      event: 'Add an event for...',
      task: 'Remind me to...',
      shopping: 'Add to shopping list...'
    };
    this.openChatOverlay();
    this.chatInput.value = prompts[type] || '';
  },
  toggleTask(el) {
    el.classList.toggle('completed');
    if (this.state.backend) fetch('/api/tasks/update', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:el.dataset.id, done:el.classList.contains('completed')})}).catch(()=>{});
  },
  addShoppingItem() {
    const input = document.getElementById('shoppingInput');
    const text = input.value.trim();
    if (!text) return;
    const li = document.createElement('li');
    li.innerHTML = `<label><input type="checkbox"> ${text}</label> <button class="icon-btn delete">✕</button>`;
    this.shoppingList.appendChild(li);
    if (this.state.backend) fetch('/api/shopping/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({item:text})}).catch(()=>{});
    input.value = '';
  },
  toggleShoppingItem(li) {
    li.classList.toggle('completed');
  },
  async loadTasks() {
    let tasks = [
      {id:1, text:'Check emails', done:false},
      {id:2, text:'Buy groceries', done:false}
    ];
    if (this.state.backend) {
      try {
        const r = await fetch('/api/tasks/today');
        const data = await r.json();
        tasks = data.tasks || data;
      } catch {}
    }
    this.taskList.innerHTML='';
    this.tasksAll.innerHTML='';
    tasks.forEach(t => {
      const li = document.createElement('li');
      li.textContent = t.text || t.title;
      li.dataset.id = t.id || t.text;
      if (t.done || t.completed) li.classList.add('completed');
      const li2 = li.cloneNode(true);
      this.taskList.appendChild(li);
      this.tasksAll.appendChild(li2);
    });
  },
  async loadEvents() {
    let events = this.demoEvents.slice();
    if (this.state.backend) {
      try {
        const r = await fetch('/api/events/upcoming');
        const data = await r.json();
        events = data.events || data;
      } catch {}
    }
    this.eventList.innerHTML='';
    events.forEach(e => {
      const li = document.createElement('li');
      li.textContent = e.title || e.name;
      this.eventList.appendChild(li);
    });
    this.demoEvents = events.map(e=>({date:e.date || e.start_date || e.start || new Date().toISOString().split('T')[0], title:e.title || e.name}));
    this.calendar.render();
  },
  calendar: {
    month: new Date(),
    build() { this.render(); },
    prev() { this.month.setMonth(this.month.getMonth()-1); this.render(); },
    next() { this.month.setMonth(this.month.getMonth()+1); this.render(); },
    selectDay(date) { this.selected = date; ZoeUI.calendar.renderDay(); },
    render() {
      const year = this.month.getFullYear();
      const month = this.month.getMonth();
      document.getElementById('calMonth').textContent = this.month.toLocaleString('default',{month:'long', year:'numeric'});
      const first = new Date(year, month, 1);
      const start = first.getDay();
      const grid = document.getElementById('calGrid');
      grid.innerHTML='';
      const days = new Date(year, month+1, 0).getDate();
      for(let i=0;i<start;i++) grid.appendChild(document.createElement('div'));
      for(let d=1; d<=days; d++) {
        const date = new Date(year, month, d);
        const cell = document.createElement('div');
        cell.className='calendar-day';
        cell.textContent=d;
        if (this.isToday(date)) cell.classList.add('today');
        if (this.selected && this.sameDay(date,this.selected)) cell.classList.add('selected');
        const ds = date.toISOString().split('T')[0];
        const hasEvent = ZoeUI.demoEvents.some(e=>e.date===ds);
        if (hasEvent) cell.classList.add('has-event');
        cell.addEventListener('click', ()=>{ ZoeUI.calendar.selectDay(date); });
        grid.appendChild(cell);
      }
      if (!this.selected || this.selected.getMonth()!==month) this.selected = new Date();
      this.renderDay();
    },
    renderDay() {
      const title = document.getElementById('dayTitle');
      title.textContent = this.selected.toDateString();
      const list = ZoeUI.dayEvents; list.innerHTML='';
      const ds = this.selected.toISOString().split('T')[0];
      ZoeUI.demoEvents.filter(e=>e.date===ds).forEach(ev=>{
        const li=document.createElement('li'); li.textContent=ev.title; list.appendChild(li);});
    },
    isToday(d){ const n=new Date(); return this.sameDay(d,n); },
    sameDay(a,b){ return a.getFullYear()==b.getFullYear() && a.getMonth()==b.getMonth() && a.getDate()==b.getDate(); }
  },
  demoEvents: [
    {date: new Date().toISOString().split('T')[0], title:'Demo Event'}
  ],
  toast(msg, ms=1500){
    this.toastEl.textContent = msg;
    this.toastEl.classList.remove('hidden');
    setTimeout(()=>this.toastEl.classList.add('hidden'), ms);
  }
};

window.addEventListener('DOMContentLoaded', () => ZoeUI.init());
