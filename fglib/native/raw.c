#define _GNU_SOURCE
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <trace-cmd.h>
#include <trace-seq.h>

extern const char *tracecmd_get_trace_clock(struct tracecmd_input *handle);

struct module_symbol {
    unsigned long long address;
    char *module;
};

struct decoder {
    unsigned long graph;
    unsigned long sched;
    unsigned long other;
    struct module_symbol *modules;
    size_t module_count;
};

static int compare_symbols(const void *left, const void *right)
{
    const struct module_symbol *first = left;
    const struct module_symbol *second = right;
    return (first->address > second->address) - (first->address < second->address);
}

static int load_modules(struct tep_handle *tep, struct decoder *state)
{
    FILE *table = tmpfile();
    if (!table)
        return -1;
    int saved = dup(STDOUT_FILENO);
    if (saved < 0 || fflush(stdout) || dup2(fileno(table), STDOUT_FILENO) < 0) {
        if (saved >= 0)
            close(saved);
        fclose(table);
        return -1;
    }
    tep_print_funcs(tep);
    int failed = fflush(stdout);
    if (dup2(saved, STDOUT_FILENO) < 0)
        failed = 1;
    close(saved);
    rewind(table);
    char *line = NULL;
    size_t capacity = 0;
    size_t module_capacity = 0;
    while (!failed && getline(&line, &capacity, table) >= 0) {
        char *module = strstr(line, " [");
        if (!module)
            continue;
        char *end = strchr(module + 2, ']');
        if (!end) {
            failed = 1;
            break;
        }
        *end = 0;
        if (state->module_count == module_capacity) {
            size_t enlarged = module_capacity ? module_capacity * 2 : 64;
            struct module_symbol *grown = realloc(state->modules, enlarged * sizeof(*grown));
            if (!grown) {
                failed = 1;
                break;
            }
            state->modules = grown;
            module_capacity = enlarged;
        }
        char *name = strdup(module + 2);
        if (!name) {
            failed = 1;
            break;
        }
        state->modules[state->module_count++] = (struct module_symbol){strtoull(line, NULL, 16), name};
    }
    failed |= ferror(table);
    free(line);
    fclose(table);
    if (state->module_count)
        qsort(state->modules, state->module_count, sizeof(*state->modules), compare_symbols);
    return failed ? -1 : 0;
}

static int number(struct tep_event *event, struct tep_record *record,
                  const char *name, unsigned long long *value)
{
    struct tep_format_field *field = tep_find_any_field(event, name);
    if (!field || field->offset < 0 || field->size < 1 || field->size > 8 ||
        field->offset > record->size - field->size)
        return -1;
    return tep_read_number_field(field, record->data, value);
}

static void safe_text(const char *text, size_t length)
{
    for (size_t index = 0; index < length && text[index]; index++) {
        unsigned char character = text[index];
        putchar(character < 32 || character == 127 || character == '|' ||
                character == '*' ? '?' : character);
    }
}

static int text_field(struct tep_event *event, struct tep_record *record,
                      const char *name, char *output, size_t capacity)
{
    struct tep_format_field *field = tep_find_field(event, name);
    if (!field || field->offset < 0 || field->size < 1 ||
        field->offset > record->size - field->size)
        return -1;
    size_t length = strnlen((char *)record->data + field->offset, field->size);
    if (length >= capacity)
        return -1;
    memcpy(output, (char *)record->data + field->offset, length);
    output[length] = 0;
    return 0;
}

static void module_name(struct tep_handle *tep, unsigned long long address, struct decoder *state)
{
    if (!state->module_count)
        return;
    struct module_symbol key = {tep_find_function_address(tep, address), NULL};
    struct module_symbol *match = bsearch(&key, state->modules, state->module_count,
                                          sizeof(*state->modules), compare_symbols);
    if (match) {
        printf(" [");
        safe_text(match->module, strlen(match->module));
        putchar(']');
    }
}

static void symbol(struct tep_handle *tep, unsigned long long address, struct decoder *state)
{
    const char *name = tep_find_function(tep, address);
    if (name)
        safe_text(name, strlen(name));
    else
        printf("unresolved_0x%llx", address);
    module_name(tep, address, state);
}

static void prefix(struct tep_handle *tep, struct tep_record *record, int cpu,
                   int pid, const char *comm, int latency)
{
    printf("%llu.%09llu |  %d) ", record->ts / 1000000000,
           record->ts % 1000000000, cpu);
    safe_text(comm, strlen(comm));
    printf("-%d | ", pid);
    if (latency) {
        putchar(' ');
        struct trace_seq sequence;
        trace_seq_init(&sequence);
        tep_print_event(tep, &sequence, record, "%s", TEP_PRINT_LATENCY);
        safe_text(sequence.buffer, sequence.len);
        printf(" | ");
        trace_seq_destroy(&sequence);
    }
}

static int arguments(struct tep_handle *tep, struct tep_event *event,
                     struct tep_record *record)
{
    struct tep_format_field *field = tep_find_field(event, "args");
    if (!field)
        return 0;
    int width = tep_get_long_size(tep);
    int offset = field->offset;
    int length = record->size - offset;
    if (field->flags & TEP_FIELD_IS_DYNAMIC) {
        unsigned long long location;
        if (number(event, record, "args", &location))
            return -1;
        offset = location & 0xffff;
        length = location >> 16;
        if (field->flags & TEP_FIELD_IS_RELATIVE)
            offset += field->offset + field->size;
    } else if (field->size) {
        length = field->size;
    }
    if ((width != 4 && width != 8) || offset < 0 || length < 0 ||
        offset > record->size - length || length % width || length / width > 64)
        return -1;
    for (int index = 0; index < length / width; index++) {
        if (index)
            printf(", ");
        printf("arg%d=0x%llx", index, tep_read_number(tep,
               (char *)record->data + offset + index * width, width));
    }
    return 0;
}

static int graph_event(struct tep_handle *tep, struct tep_event *event,
                       struct tep_record *record, struct decoder *state)
{
    unsigned long long address, depth, start, end, value;
    if (number(event, record, "func", &address) ||
        number(event, record, "depth", &depth) || depth > 4096)
        return -1;
    if (!strcmp(event->name, "funcgraph_exit")) {
        if (number(event, record, "calltime", &start) ||
            number(event, record, "rettime", &end) || end < start)
            return -1;
        unsigned long long duration = end - start;
        printf(" %llu.%03llu us   |  %*s} /* ", duration / 1000,
               duration % 1000, (int)depth * 2, "");
        symbol(tep, address, state);
        if (tep_find_field(event, "retval")) {
            if (number(event, record, "retval", &value))
                return -1;
            printf(" ret=0x%llx", value);
        }
        printf(" */\n");
        if (!number(event, record, "overrun", &value) && value)
            printf("# raw: function_graph overrun=%llu (calls may be incomplete)\n", value);
    } else {
        printf("              |  %*s", (int)depth * 2, "");
        symbol(tep, address, state);
        putchar('(');
        if (arguments(tep, event, record))
            return -1;
        printf(") {");
        if (tep_find_field(event, "retaddr")) {
            if (number(event, record, "retaddr", &value))
                return -1;
            const char *name = NULL;
            unsigned long long base = 0;
            unsigned long size = 0;
            if (tep_find_function_info(tep, value, &name, &base, &size) && name) {
                printf(" /* <-");
                safe_text(name, strlen(name));
                printf("+0x%llx/0x%lx", value - base, size);
                module_name(tep, value, state);
                printf(" */");
            } else {
                printf(" /* raw retaddr=0x%llx */", value);
            }
        }
        putchar('\n');
    }
    state->graph++;
    return 0;
}

static int sched_event(struct tep_handle *tep, struct tep_event *event,
                       struct tep_record *record, int cpu)
{
    char previous[256], next[256];
    unsigned long long prev_pid, next_pid, prev_state, priority, next_priority;
    if (text_field(event, record, "prev_comm", previous, sizeof(previous)) ||
        text_field(event, record, "next_comm", next, sizeof(next)) ||
        number(event, record, "prev_pid", &prev_pid) ||
        number(event, record, "next_pid", &next_pid) ||
        number(event, record, "prev_state", &prev_state) ||
        number(event, record, "prev_prio", &priority) ||
        number(event, record, "next_prio", &next_priority))
        return -1;
    struct trace_seq sequence;
    trace_seq_init(&sequence);
    tep_print_event(tep, &sequence, record, "%s", TEP_PRINT_INFO);
    printf("              |  /* sched_switch: ");
    safe_text(sequence.buffer, sequence.len);
    printf(" raw_prev_state=0x%llx */\n", prev_state);
    trace_seq_destroy(&sequence);
    printf(" %d) ", cpu);
    safe_text(previous, strlen(previous));
    printf("-%llu => ", prev_pid);
    safe_text(next, strlen(next));
    printf("-%llu\n", next_pid);
    tep_override_comm(tep, previous, prev_pid);
    tep_override_comm(tep, next, next_pid);
    prefix(tep, record, cpu, next_pid, next, 0);
    printf("              |  /* raw: switch-in */\n");
    return 0;
}

static int visit(struct tracecmd_input *handle, struct tep_record *record,
                 int cpu, void *data)
{
    struct decoder *state = data;
    struct tep_handle *tep = tracecmd_get_tep(handle);
    struct tep_event *event = tep_find_event_by_record(tep, record);
    unsigned long long pid;
    unsigned long long common;
    if (!event || number(event, record, "common_pid", &pid) || pid > 2147483647 ||
        number(event, record, "common_flags", &common) ||
        number(event, record, "common_preempt_count", &common))
        goto malformed;
    if (!strcmp(event->system, "sched") && (event->flags & TEP_EVENT_FL_FAILED))
        goto malformed;
    if (record->missed_events > 0)
        printf("CPU:%d [LOST %lld EVENTS]\n", cpu, record->missed_events);
    else if (record->missed_events < 0)
        printf("CPU:%d [LOST ? EVENTS]\n", cpu);
    const char *comm = tep_data_comm_from_pid(tep, pid);
    prefix(tep, record, cpu, pid, comm ? comm : "<unknown>", 1);
    if (!strcmp(event->system, "ftrace") &&
        (!strcmp(event->name, "funcgraph_entry") ||
         !strcmp(event->name, "fgraph_retaddr_entry") ||
         !strcmp(event->name, "funcgraph_exit"))) {
        if (graph_event(tep, event, record, state))
            goto malformed;
    } else if (!strcmp(event->system, "sched") && !strcmp(event->name, "sched_switch")) {
        if (sched_event(tep, event, record, cpu))
            goto malformed;
        state->sched++;
    } else {
        struct trace_seq sequence;
        trace_seq_init(&sequence);
        tep_print_event(tep, &sequence, record, "%s", TEP_PRINT_INFO);
        printf("              |  /* %s: ", event->name);
        safe_text(sequence.buffer, sequence.len);
        printf(" */\n");
        trace_seq_destroy(&sequence);
        state->other++;
        if (!strcmp(event->system, "sched") &&
            (!strcmp(event->name, "sched_waking") || !strcmp(event->name, "sched_wakeup") ||
             !strcmp(event->name, "sched_wakeup_new")))
            state->sched++;
    }
    return ferror(stdout) ? -1 : 0;
malformed:
    fprintf(stderr, "invalid or unsupported event fields on CPU %d at %llu\n", cpu, record->ts);
    return -1;
}

int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3)
        return 2;
    struct tracecmd_input *root = tracecmd_open(argv[1], TRACECMD_FL_LOAD_NO_PLUGINS);
    if (!root) {
        fprintf(stderr, "invalid or unsupported trace-cmd container\n");
        return 1;
    }
    int status = 1;
    struct decoder state = {0};
    struct tracecmd_input *selected = root;
    if (argc == 3) {
        selected = NULL;
        for (int index = 0; index < tracecmd_buffer_instances(root); index++) {
            if (!strcmp(argv[2], tracecmd_buffer_instance_name(root, index))) {
                selected = tracecmd_buffer_instance_handle(root, index);
                break;
            }
        }
        if (!selected) {
            fprintf(stderr, "raw instance not found: %s\n", argv[2]);
            goto done;
        }
    }
    if (load_modules(tracecmd_get_tep(selected), &state)) {
        fprintf(stderr, "cannot read module symbol ownership\n");
        goto done;
    }
    const char *clock = tracecmd_get_trace_clock(selected);
    if (clock && strcmp(clock, "local") && strcmp(clock, "global") &&
               strcmp(clock, "mono") && strcmp(clock, "mono_raw") &&
               strcmp(clock, "boot") && strcmp(clock, "tai") &&
               strcmp(clock, "uptime") && strcmp(clock, "perf")) {
        fprintf(stderr, "unsupported raw trace clock: %s (capture with -C mono)\n", clock);
        goto done;
    }
    printf("# tracer: function_graph\n# raw: trace-cmd container; timestamps in ns; clock=%s\n",
           clock ? clock : "local (legacy default)");
    printf("# raw: args are untyped register values; retval is an untyped bit pattern\n");
    if (tracecmd_iterate_events(selected, NULL, 0, visit, &state) < 0)
        goto done;
    if (!state.graph && !state.sched) {
        fprintf(stderr, "no function_graph or scheduling events in selected buffer\n");
        for (int index = 0; index < tracecmd_buffer_instances(root); index++)
            fprintf(stderr, "available --raw-instance %s\n", tracecmd_buffer_instance_name(root, index));
        goto done;
    }
    status = fflush(stdout) || ferror(stdout) ? 1 : 0;
done:
    for (size_t index = 0; index < state.module_count; index++)
        free(state.modules[index].module);
    free(state.modules);
    if (selected && selected != root)
        tracecmd_close(selected);
    tracecmd_close(root);
    return status;
}
