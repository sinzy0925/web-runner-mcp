(function() {
    console.log("[JavaScript] Script loaded and running.");

    function getSelector(element) {
        let path = [];
        while (element) {
            let selector = element.tagName.toLowerCase();

            if (element.id) {
                selector += `#${element.id}`;
            } else if (element.className) {
                selector += `.${element.className.replace(/ /g, '.')}`;
            } else {
                let index = 1;
                let sibling = element.previousElementSibling;
                while (sibling) {
                    if (sibling.tagName === element.tagName) {
                        index++;
                    }
                    sibling = sibling.previousElementSibling;
                }
                selector += `:nth-of-type(${index})`;
            }

            path.unshift(selector);
            element = element.parentNode;
        }
        return path.slice(1).join(' > ');
    }

    // DOMContentLoaded イベントを使用して、DOM がロードされた後にイベントリスナーを登録する
    document.addEventListener('DOMContentLoaded', function() {
        console.log("[JavaScript] DOMContentLoaded event fired.");

        // イベント委譲を使用する
        document.body.addEventListener('click', function(event) {
            const target = event.target;
            const selector = getSelector(target);
            window.clickSelector = selector;
            console.log("[JavaScript] Click event on:", selector, target);
        });

        document.body.addEventListener('focus', function(event) {
            const target = event.target;
            const selector = getSelector(target);
            window.focusSelector = selector;
            console.log("[JavaScript] Focus event on:", selector, target);
        }, true); // キャプチャフェーズを使用

         document.body.addEventListener('blur', function(event) {
            const target = event.target;
            const selector = getSelector(target);
            window.blurSelector = selector;
            console.log("[JavaScript] Blur event on:", selector, target);
        }, true); // キャプチャフェーズを使用

        // 他のイベントも同様に処理する
        console.log("[JavaScript] Event listeners added.");
    });
})();