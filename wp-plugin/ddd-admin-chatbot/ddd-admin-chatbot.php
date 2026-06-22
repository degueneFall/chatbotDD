<?php
/**
 * Plugin Name: DDD Chatbot Admin
 * Plugin URI:  https://demdikk.sn
 * Description: Interface d'administration pour les questions sans réponse du chatbot Dakar Dem Dikk.
 * Version:     1.0.0
 * Author:      Dakar Dem Dikk
 * Text Domain: ddd-chatbot-admin
 */

defined('ABSPATH') || exit;

// ── Configuration ─────────────────────────────────────────────────────────────
// À définir dans wp-config.php ou via l'interface Réglages
define('DDD_CHATBOT_API_BASE', get_option('ddd_chatbot_api_base', 'https://chatbot.demdikk.sn'));
define('DDD_CHATBOT_TOKEN',    get_option('ddd_chatbot_token',    ''));

// ── Menu Admin WordPress ──────────────────────────────────────────────────────
add_action('admin_menu', function () {
    add_menu_page(
        'Chatbot DDD',
        'Chatbot DDD',
        'manage_options',
        'ddd-chatbot-admin',
        'ddd_chatbot_admin_page',
        'dashicons-format-chat',
        30
    );
    add_submenu_page(
        'ddd-chatbot-admin',
        'Questions sans réponse',
        'Questions',
        'manage_options',
        'ddd-chatbot-admin',
        'ddd_chatbot_admin_page'
    );
    add_submenu_page(
        'ddd-chatbot-admin',
        'Réglages',
        'Réglages',
        'manage_options',
        'ddd-chatbot-settings',
        'ddd_chatbot_settings_page'
    );
});

// ── Enqueue assets ────────────────────────────────────────────────────────────
add_action('admin_enqueue_scripts', function ($hook) {
    if (!in_array($hook, ['toplevel_page_ddd-chatbot-admin', 'chatbot-ddd_page_ddd-chatbot-admin'])) return;
    wp_enqueue_script(
        'ddd-chatbot-admin-js',
        plugin_dir_url(__FILE__) . 'assets/admin.js',
        [],
        '1.0.0',
        true
    );
    wp_localize_script('ddd-chatbot-admin-js', 'DDD_ADMIN', [
        'api_base' => DDD_CHATBOT_API_BASE,
        'token'    => DDD_CHATBOT_TOKEN,
        'ajax_url' => admin_url('admin-ajax.php'),
        'nonce'    => wp_create_nonce('ddd_chatbot_nonce'),
    ]);
    wp_enqueue_style(
        'ddd-chatbot-admin-css',
        plugin_dir_url(__FILE__) . 'assets/admin.css',
        [],
        '1.0.0'
    );
});

// ── Page principale : questions sans réponse ──────────────────────────────────
function ddd_chatbot_admin_page() {
    if (!current_user_can('manage_options')) {
        wp_die('Accès refusé.');
    }

    // Récupérer les pages WordPress publiées
    $wp_pages = get_pages(['post_status' => 'publish', 'sort_column' => 'post_title']);
    $pages_options = '';
    foreach ($wp_pages as $p) {
        $pages_options .= sprintf(
            '<option value="%d" data-url="%s">%s</option>',
            $p->ID,
            esc_attr(get_permalink($p->ID)),
            esc_html($p->post_title)
        );
    }
    $posts = get_posts(['post_status' => 'publish', 'numberposts' => 100, 'orderby' => 'title', 'order' => 'ASC']);
    foreach ($posts as $p) {
        $pages_options .= sprintf(
            '<option value="%d" data-url="%s">[Article] %s</option>',
            $p->ID,
            esc_attr(get_permalink($p->ID)),
            esc_html($p->post_title)
        );
    }
    ?>
    <div class="wrap ddd-admin-wrap">
        <h1>🤖 Chatbot DDD — Questions sans réponse</h1>

        <?php if (empty(DDD_CHATBOT_TOKEN)): ?>
            <div class="notice notice-error"><p>⚠️ Token non configuré. Allez dans <a href="<?php echo admin_url('admin.php?page=ddd-chatbot-settings'); ?>">Réglages</a>.</p></div>
        <?php endif; ?>

        <div id="ddd-stats" class="ddd-stats">Chargement…</div>

        <div class="ddd-filters">
            <label>Filtre :
                <select id="ddd-filter-status">
                    <option value="all">Tous</option>
                    <option value="en_attente">En attente</option>
                    <option value="repondu">Répondus</option>
                    <option value="redirige">Redirigés</option>
                </select>
            </label>
            <input type="text" id="ddd-search" placeholder="Rechercher une question…">
            <button class="button" onclick="dddRefresh()">↺ Actualiser</button>
        </div>

        <div id="ddd-questions-list">
            <p>Chargement des questions…</p>
        </div>

        <!-- Template modal Répondre -->
        <div id="ddd-modal-reponse" class="ddd-modal" style="display:none">
            <div class="ddd-modal-inner">
                <h2>✏️ Ajouter une réponse</h2>
                <p id="ddd-modal-question-text" class="ddd-question-preview"></p>
                <input type="hidden" id="ddd-modal-uid">

                <label>Réponse :</label>
                <textarea id="ddd-modal-reponse-text" rows="6" placeholder="Saisissez la réponse à afficher dans le chatbot…"></textarea>

                <label>Ajouter aussi dans une page WordPress (optionnel) :</label>
                <select id="ddd-modal-page-id">
                    <option value="">— Ne pas ajouter dans une page —</option>
                    <?php echo $pages_options; ?>
                </select>

                <div class="ddd-modal-actions">
                    <button class="button button-primary" onclick="dddSubmitReponse()">✓ Enregistrer la réponse</button>
                    <button class="button" onclick="dddCloseModal('ddd-modal-reponse')">Annuler</button>
                </div>
                <div id="ddd-modal-reponse-msg" class="ddd-msg"></div>
            </div>
        </div>

        <!-- Template modal Rediriger -->
        <div id="ddd-modal-redirect" class="ddd-modal" style="display:none">
            <div class="ddd-modal-inner">
                <h2>↗️ Rediriger vers une page</h2>
                <p id="ddd-modal-redirect-question" class="ddd-question-preview"></p>
                <input type="hidden" id="ddd-modal-redirect-uid">

                <label>Page cible :</label>
                <select id="ddd-modal-redirect-page-id">
                    <option value="">— Choisir une page —</option>
                    <?php echo $pages_options; ?>
                </select>

                <div class="ddd-modal-actions">
                    <button class="button button-primary" onclick="dddSubmitRedirect()">↗ Enregistrer la redirection</button>
                    <button class="button" onclick="dddCloseModal('ddd-modal-redirect')">Annuler</button>
                </div>
                <div id="ddd-modal-redirect-msg" class="ddd-msg"></div>
            </div>
        </div>

        <div id="ddd-modal-overlay" class="ddd-modal-overlay" style="display:none" onclick="dddCloseAllModals()"></div>
    </div>
    <?php
}

// ── Page Réglages ─────────────────────────────────────────────────────────────
function ddd_chatbot_settings_page() {
    if (!current_user_can('manage_options')) wp_die('Accès refusé.');

    if (isset($_POST['ddd_save_settings']) && check_admin_referer('ddd_settings_nonce')) {
        update_option('ddd_chatbot_api_base', sanitize_text_field($_POST['ddd_api_base']));
        update_option('ddd_chatbot_token',    sanitize_text_field($_POST['ddd_token']));
        echo '<div class="notice notice-success"><p>Réglages enregistrés.</p></div>';
    }
    $api_base = get_option('ddd_chatbot_api_base', 'https://chatbot.demdikk.sn');
    $token    = get_option('ddd_chatbot_token', '');
    ?>
    <div class="wrap">
        <h1>Réglages — Chatbot DDD</h1>
        <form method="post">
            <?php wp_nonce_field('ddd_settings_nonce'); ?>
            <table class="form-table">
                <tr>
                    <th><label for="ddd_api_base">URL de l'API Flask</label></th>
                    <td>
                        <input type="url" name="ddd_api_base" id="ddd_api_base"
                               value="<?php echo esc_attr($api_base); ?>" class="regular-text">
                        <p class="description">Ex : https://chatbot.demdikk.sn</p>
                    </td>
                </tr>
                <tr>
                    <th><label for="ddd_token">Token d'authentification</label></th>
                    <td>
                        <input type="text" name="ddd_token" id="ddd_token"
                               value="<?php echo esc_attr($token); ?>" class="regular-text">
                        <p class="description">Valeur du REFRESH_TOKEN dans le .env du chatbot.</p>
                    </td>
                </tr>
            </table>
            <p class="submit">
                <button type="submit" name="ddd_save_settings" class="button button-primary">Enregistrer</button>
            </p>
        </form>
    </div>
    <?php
}

// ── AJAX : injecter Q/R dans le contenu d'une page WordPress ─────────────────
add_action('wp_ajax_ddd_inject_qa', function () {
    check_ajax_referer('ddd_chatbot_nonce', 'nonce');
    if (!current_user_can('manage_options')) wp_die('Accès refusé.');

    $page_id      = intval($_POST['page_id'] ?? 0);
    $question     = sanitize_textarea_field($_POST['question'] ?? '');
    $reponse_text = sanitize_textarea_field($_POST['reponse_text'] ?? '');

    if (!$page_id || !$question || !$reponse_text) {
        wp_send_json_error('Données manquantes.');
    }

    $post = get_post($page_id);
    if (!$post) {
        wp_send_json_error('Page introuvable.');
    }

    // Ajouter le bloc Q/R à la fin du contenu existant
    $new_block = "\n\n<!-- DDD Chatbot Q/R -->\n"
        . "<h4>" . esc_html($question) . "</h4>\n"
        . "<p>" . nl2br(esc_html($reponse_text)) . "</p>";

    $updated = wp_update_post([
        'ID'           => $page_id,
        'post_content' => $post->post_content . $new_block,
    ], true);

    if (is_wp_error($updated)) {
        wp_send_json_error($updated->get_error_message());
    }

    wp_send_json_success(['page_url' => get_permalink($page_id)]);
});

// ── Webhook : notifier le chatbot à chaque publication/modification ────────────

/**
 * Envoi asynchrone (non-bloquant) du webhook vers Flask.
 * Déclenché à chaque fois qu'un article/page passe en statut "publish".
 */
add_action('save_post', function ($post_id, $post, $update) {
    // Ignorer les autosaves et les révisions
    if (defined('DOING_AUTOSAVE') && DOING_AUTOSAVE) return;
    if (wp_is_post_revision($post_id))               return;
    if ($post->post_status !== 'publish')            return;

    // Types de contenus à surveiller
    $watched = ['post', 'page'];
    if (!in_array($post->post_type, $watched, true)) return;

    $api_base = rtrim(DDD_CHATBOT_API_BASE, '/');
    $token    = DDD_CHATBOT_TOKEN;
    $url      = $api_base . '/webhook/content-updated';

    $payload = wp_json_encode([
        'post_id'   => $post_id,
        'post_type' => $post->post_type,
        'post_title'=> $post->post_title,
        'token'     => $token,
    ]);

    // wp_remote_post en mode non-bloquant (blocking=false)
    wp_remote_post($url, [
        'method'    => 'POST',
        'timeout'   => 1,        // On n'attend pas la réponse
        'blocking'  => false,    // Non-bloquant → n'impacte pas le temps de sauvegarde
        'headers'   => [
            'Content-Type'  => 'application/json',
            'Authorization' => 'Bearer ' . $token,
        ],
        'body'      => $payload,
    ]);

    // Log local WordPress (visible dans Outils > Santé du site si WP_DEBUG_LOG actif)
    if (defined('WP_DEBUG') && WP_DEBUG) {
        error_log('[DDD Chatbot] Webhook envoyé pour post_id=' . $post_id);
    }

}, 10, 3);
