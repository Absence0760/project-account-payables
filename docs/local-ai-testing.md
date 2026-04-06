# Local AI Extraction Testing

Test invoice extraction locally using Ollama — no API keys, no costs, no data leaving your machine.

## Prerequisites

- macOS, Linux, or WSL
- ~8GB free disk space (for the vision model)
- ~8GB RAM minimum (16GB recommended)

## Setup

### 1. Install Ollama

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Verify
ollama --version
```

### 2. Start Ollama

```bash
ollama serve
```

This runs in the foreground. Open a new terminal for the next steps. Alternatively, on macOS you can run the Ollama desktop app which starts the server automatically.

### 3. Pull a Vision Model

```bash
# Recommended — best balance of speed and accuracy
ollama pull llama3.2-vision:11b     # ~7GB download

# Alternative — higher accuracy, needs more RAM (32GB+)
ollama pull llama3.2-vision:90b     # ~55GB download

# Alternative — lighter, less accurate
ollama pull llava:13b               # ~8GB download
```

### 4. Verify the Model

```bash
# List installed models
ollama list

# Quick test — should respond with text
ollama run llama3.2-vision:11b "What is 2+2?"
```

### 5. Configure the App

In the app, go to **Organization > AI Extraction**:

1. Set **Program** to "Bring Your Own Key (free)"
2. Set **Provider** to "Ollama (Local)"
3. Set **Ollama URL** to `http://localhost:11434` (default)
4. Set **Model** to the one you pulled
5. Click **Save Extraction Settings**

### 6. Test It

1. Go to **Invoices**
2. Click **+ Upload Invoice**
3. Upload a PDF or image of an invoice
4. The extraction will run against your local model
5. Check the extracted fields in the invoice modal

## Troubleshooting

### "Cannot connect to Ollama"

Ollama server isn't running:
```bash
ollama serve
```

Or check if it's running:
```bash
curl http://localhost:11434/api/tags
```

### "Model not found"

The model isn't pulled yet:
```bash
ollama pull llama3.2-vision:11b
```

List what's installed:
```bash
ollama list
```

### Extraction is slow

Local models are slower than cloud APIs. Expected times:

| Model | Time per invoice | RAM needed |
|---|---|---|
| llava:13b | 15-30 seconds | 8GB |
| llama3.2-vision:11b | 10-25 seconds | 8GB |
| llama3.2-vision:90b | 30-60 seconds | 32GB+ |

Tips:
- Close other RAM-heavy apps
- Use the 11B model for faster iteration
- GPU acceleration helps significantly (Apple Silicon Macs use Metal automatically)

### Low extraction accuracy

Local models are less accurate than Claude Vision or GPT-4V. If results are poor:
- Try a larger model (`llama3.2-vision:90b`)
- Use a higher quality scan/photo
- Ensure the invoice is well-lit and not rotated
- Consider using Claude Vision (platform mode) for production

### PDF extraction doesn't work

Most Ollama vision models work with images, not PDFs directly. If PDF extraction fails:
1. Convert the PDF to an image first (the system should handle this automatically in future)
2. Or upload a PNG/JPG of the invoice instead of PDF

## Using Existing GGUF Models

If you have GGUF model files (e.g., from HuggingFace), you can import them into Ollama:

```bash
# Create a Modelfile
echo 'FROM /path/to/your/model.gguf' > Modelfile

# Import
ollama create my-model -f Modelfile

# Use it
ollama run my-model "test"
```

**Note:** Only vision-capable models (LLaVA, Llama 3.2 Vision, etc.) can extract data from invoice images. Text-only models (Llama 2, Qwen, Gemma, etc.) cannot process images.

## Comparison: Local vs Cloud

| | Ollama (Local) | Claude Vision (Platform) | BYOK (Cloud) |
|---|---|---|---|
| **Cost** | Free | Per-extraction fee | Your API costs |
| **Speed** | 10-30 sec | 2-5 sec | 2-5 sec |
| **Accuracy** | Good (85-90%) | Excellent (95%+) | Very good (90-95%) |
| **Privacy** | Data stays local | Data sent to Anthropic | Data sent to your provider |
| **Setup** | Install Ollama + model | None (platform key) | Enter your API key |
| **Best for** | Development, testing, privacy-sensitive | Production | Customers with existing AI contracts |

## Recommended Workflow

1. **Development:** Use Ollama locally — free, fast iteration, no API costs
2. **Staging:** Switch to Claude Vision (platform) to test production accuracy
3. **Production:** Platform mode (you bill per extraction) or BYOK (customer's key)

## Quick Reference

```bash
# Install
brew install ollama

# Start server
ollama serve

# Pull vision model
ollama pull llama3.2-vision:11b

# Check what's installed
ollama list

# Test a model
ollama run llama3.2-vision:11b "Describe this image" --images /path/to/invoice.png

# Check server is running
curl http://localhost:11434/api/tags

# Stop server
# Ctrl+C in the terminal running 'ollama serve'
# Or: pkill ollama
```
