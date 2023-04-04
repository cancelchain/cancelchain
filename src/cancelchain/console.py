from rich.console import Console
from rich.theme import Theme

theme = Theme({
    'info': 'dim cyan',
    'important': 'bold',
    'success': 'bold green',
    'error': 'bold red',
    'milling': 'cyan',
    'progress.elapsed': 'none',
    'progress.percentage': 'none',
    'progress.remaining': 'none'
})

console = Console(theme=theme, highlight=False)
