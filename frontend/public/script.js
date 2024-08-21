document.addEventListener('DOMContentLoaded', () => {
    const fileInput = document.getElementById('fileInput');
    const analyzeButton = document.getElementById('analyzeButton');
    const loadingSpinner = document.getElementById('loadingSpinner');
    const categoriesDiv = document.getElementById('categories');
    const slideout = document.createElement('div');
    slideout.className = 'slideout';
    document.body.appendChild(slideout);

    let processedData = null;
    let currentPdfFilename = '';

    analyzeButton.addEventListener('click', analyzePDF);

    async function analyzePDF() {
        const file = fileInput.files[0];
        if (!file) {
            alert('Please select a PDF file');
            return;
        }

        loadingSpinner.style.display = 'block';
        analyzeButton.disabled = true;

        const formData = new FormData();
        formData.append('file', file);

        try {
            console.log('Starting PDF analysis');
            // Process PDF
            const pdfResponse = await fetch('http://localhost:5000/process_pdf', {
                method: 'POST',
                body: formData,
            });
            const pdfData = await pdfResponse.json();
            console.log('PDF analysis response:', pdfData);

            console.log('Fetching processed data');
            // Fetch processed data
            const processedDataResponse = await fetch(`http://localhost:5000${pdfData.processed_json_path}`);
            processedData = await processedDataResponse.json();
            console.log('Processed data:', processedData);

            currentPdfFilename = file.name;
            renderResults(processedData);
        } catch (error) {
            console.error('Error:', error);
            alert('An error occurred while processing the PDF');
        } finally {
            loadingSpinner.style.display = 'none';
            analyzeButton.disabled = false;
        }
    }

    function renderResults(data) {
        console.log('Rendering results:', data);
        if (data.categorized_topics && data.categorized_topics.categories) {
            renderCategories(data.categorized_topics.categories, data.common_topics);
        } else {
            console.error('Missing categorized topics data');
        }
    }

    function renderCategories(categories, commonTopics) {
        console.log('Rendering categories:', categories);
        categoriesDiv.innerHTML = '<h2>Categories</h2>';
        categories.forEach(category => {
            const categoryContainer = document.createElement('div');
            categoryContainer.className = 'category-container';

            const button = document.createElement('button');
            button.textContent = category.name;
            button.className = 'category-button';
            button.addEventListener('click', () => toggleCategoryItems(categoryContainer));
            categoryContainer.appendChild(button);

            const itemsList = document.createElement('ul');
            itemsList.className = 'category-items';
            itemsList.style.display = 'none';
            category.items.forEach(item => {
                const li = document.createElement('li');
                const itemButton = document.createElement('button');
                itemButton.textContent = item;
                itemButton.className = 'item-button';
                itemButton.addEventListener('click', (event) => renderTopicPages(item, commonTopics[item], event.target));
                li.appendChild(itemButton);
                itemsList.appendChild(li);
            });
            categoryContainer.appendChild(itemsList);

            categoriesDiv.appendChild(categoryContainer);
        });
    }

    function toggleCategoryItems(categoryContainer) {
        const itemsList = categoryContainer.querySelector('.category-items');
        if (itemsList.style.display === 'none') {
            itemsList.style.display = 'block';
        } else {
            itemsList.style.display = 'none';
        }
    }

    function renderTopicPages(topic, pages, clickedElement) {
        console.log('Rendering topic pages:', topic, pages);
        
        // Remove any existing topic pages content
        const existingTopicPages = document.querySelector('.topic-pages');
        if (existingTopicPages) {
            existingTopicPages.remove();
        }

        const topicPagesDiv = document.createElement('div');
        topicPagesDiv.className = 'topic-pages';
        topicPagesDiv.innerHTML = `<h3>Pages for Topic: ${topic}</h3>`;
        
        const pagesList = document.createElement('ul');
        pages.forEach(page => {
            const li = document.createElement('li');
            const pageButton = document.createElement('button');
            pageButton.textContent = formatPageName(page);
            pageButton.className = 'page-button';
            pageButton.addEventListener('click', (event) => {
                console.log('Page button clicked:', page);
                renderPageContent(page, event.target);
            });
            li.appendChild(pageButton);
            pagesList.appendChild(li);
        });
        topicPagesDiv.appendChild(pagesList);

        // Insert the topic pages content after the clicked element
        clickedElement.parentNode.insertBefore(topicPagesDiv, clickedElement.nextSibling);
    }

    function formatPageName(pageName) {
        return pageName.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
    }

    async function renderPageContent(page, clickedElement) {
        console.log('Rendering page content:', page);

        // Remove any existing page content
        const existingPageContent = document.querySelector('.page-content');
        if (existingPageContent) {
            existingPageContent.remove();
        }

        const pageContentDiv = document.createElement('div');
        pageContentDiv.className = 'page-content';

        if (processedData && processedData.pages && processedData.pages[page]) {
            const pageData = processedData.pages[page];
            pageContentDiv.innerHTML = `
                <h3>Page Content: ${formatPageName(page)}</h3>
                <h4>Topics:</h4>
                <ul>
                    ${pageData.topics.map(topic => `<li>${topic}</li>`).join('')}
                </ul>
                <h4>Summary:</h4>
                <p>${pageData.summary}</p>
                <h4>Tables:</h4>
                <pre>${JSON.stringify(pageData.tables, null, 2)}</pre>
            `;
        } else {
            pageContentDiv.innerHTML = `<h3>Page Content: ${formatPageName(page)}</h3>`;
            pageContentDiv.innerHTML += '<p>Page content not available.</p>';
        }

        // Insert the page content after the clicked element
        clickedElement.parentNode.insertBefore(pageContentDiv, clickedElement.nextSibling);

        // Render PDF page in slideout
        const pageNumber = parseInt(page.split('_')[1]);
        await renderPdfPage(pageNumber);
    }

    async function renderPdfPage(pageNumber) {
        console.log('Rendering PDF page:', pageNumber);
        try {
            const response = await fetch(`http://localhost:5000/get_pdf_page/${currentPdfFilename}/${pageNumber}`);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const blob = await response.blob();
            const imageUrl = URL.createObjectURL(blob);

            slideout.innerHTML = `
                <div class="slideout-content">
                    <button class="close-slideout">&times;</button>
                    <img src="${imageUrl}" alt="PDF Page ${pageNumber}" />
                </div>
            `;

            slideout.style.width = '50%';

            const closeButton = slideout.querySelector('.close-slideout');
            closeButton.addEventListener('click', () => {
                slideout.style.width = '0';
            });
        } catch (error) {
            console.error('Error rendering PDF page:', error);
            slideout.innerHTML = '<p>Error loading PDF page</p>';
            slideout.style.width = '50%';
        }
    }
});