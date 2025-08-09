import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

static_dir = os.path.join(os.path.dirname(__file__), 'static')
app.mount('/static', StaticFiles(directory=static_dir), name='static')

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), 'templates'))

@app.get('/', response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse('zoe_complete_ui.html', {'request': request})

@app.get('/calendar', response_class=HTMLResponse)
def calendar(request: Request):
    return templates.TemplateResponse('calendar.html', {'request': request})
