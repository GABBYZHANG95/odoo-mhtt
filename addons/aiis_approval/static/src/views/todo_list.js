/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";

class TodoListController extends ListController {
    setup() {
        super.setup();
        this.todoOrm = useService("orm");
        this.todoAction = useService("action");
    }

    async openRecord(record) {
        const action = await this.todoOrm.call("aiis.todo", "action_open_document", [[record.resId]]);
        await this.todoAction.doAction(action);
    }
}

registry.category("views").add("aiis_todo_list", {
    ...listView,
    Controller: TodoListController,
});
