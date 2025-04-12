# Web-Runner powered by Playwright

![Web-Runner Logo](./Web-Runner.png)

* Effortless Web Automation with Playwright & JSON. *

## Overview

Streamline your web automation tasks! Web-Runner is a powerful yet user-friendly Python application built on Playwright that allows you to define and execute complex browser interactions using a simple JSON configuration. Forget writing boilerplate Playwright code – define your automation logic declaratively and let Web-Runner handle the rest.

It's perfect for web scraping (including PDF content!), automated testing, and automating repetitive online tasks without requiring extensive Playwright expertise.

## Why Web-Runner?

*   **Simplified Workflow:** Define your automation logic declaratively using our intuitive JSON format.
*   **Visual JSON Generator:** Use the included `json_generator.html` tool to visually build your automation steps and generate the required JSON input, making setup quick and easy.
*   **Robust Action Support:** Handles a wide range of browser interactions: clicks, text input, hovering, dropdown selection, scrolling, waiting for elements/page loads, and taking screenshots.
*   **Advanced Data Extraction:** Go beyond basic scraping. Extract `innerText`, `textContent`, `innerHTML`, specific element attributes (single or multiple), and automatically resolve relative URLs to absolute ones when getting `href` attributes.
*   **Intelligent iframe Handling:** Features smart, automatic iframe scope detection, attempting to find elements even within nested frames without requiring explicit `switch_to_iframe` commands in many common scenarios. (Manual switching is also supported).
*   **Unique PDF Text Extraction:** Automatically detects `.pdf` links (when extracting `href`), downloads the file, and extracts its text content, seamlessly integrating it into your results.
*   **Asynchronous Power:** Built with `asyncio` for efficient handling of network operations and parallel processing.
*   **Debugging Made Easy:** Provides detailed logging (`playwright_runner_async.log`) and automatically saves screenshots upon errors (`screenshots/` directory).

## Ideal For

*   Developers needing to quickly automate web interactions.
*   QA Engineers setting up browser tests.
*   Data Analysts and Researchers scraping web data, including from PDFs.
*   Anyone looking to automate repetitive online tasks.

## Getting Started

### Prerequisites

*   Python 3.8+
*   Playwright browsers installed (Run `playwright install` after installing the library)

### Installation

1.  Clone this repository:
    ```bash
    git clone https://github.com/your-username/web-runner.git # Replace with your repo URL
    cd web-runner
    ```
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
    *(If you don't have a requirements.txt yet, list the command: `pip install playwright PyMuPDF`)*
3.  Install Playwright browsers:
    ```bash
    playwright install
    ```

### Usage

1.  **Create your actions JSON file:**
    *   Use the `json_generator.html` file in your browser to visually build the steps.
    *   Alternatively, manually create a JSON file (e.g., `my_task.json`) following this structure:
        ```json
        {
          "target_url": "https://example.com",
          "actions": [
            {
              "action": "input",
              "selector": "#search",
              "value": "Playwright"
            },
            {
              "action": "click",
              "selector": "button[type='submit']"
            },
            {
              "action": "get_text_content",
              "selector": "h1"
            }
            // Add more actions...
          ]
        }
        ```
2.  **Run the application:**
    ```bash
    python main.py --input my_task.json
    ```
    *   Use `--headless` to run without opening a browser window.
    *   Use `--slowmo <milliseconds>` (e.g., `--slowmo 500`) to slow down execution for observation.
3.  **Check the results:**
    *   The execution log will be printed to the console and saved to `playwright_runner_async.log`.
    *   The final extracted data and step results will be printed at the end.
    *   Any error screenshots will be saved in the `screenshots/` directory.

## Dependencies

*   [Playwright](https://playwright.dev/python/)
*   [PyMuPDF (fitz)](https://pymupdf.readthedocs.io/en/latest/)

*(You can list specific versions if needed, especially if you provide a `requirements.txt`)*

## Contributing

*(Optional: Add guidelines here if you welcome contributions)*
Contributions are welcome! Please feel free to submit a pull request or open an issue.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. 