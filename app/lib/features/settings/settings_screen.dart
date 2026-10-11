import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/api/api_client.dart';
import '../../core/app_session.dart';
import '../../core/format.dart';
import '../../core/models/safety.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/common.dart';
import '../legal/legal_doc_screen.dart';
import 'memory_screen.dart';

const scenarioLabels = {'calm': '평상시', 'rain': '비', 'wind_tomorrow': '내일 강풍', 'flood': '침수 센서', 'storm': '폭풍'};

/// 33_Settings_Demo (Figma 73:5904). 계정 · 나에 대해 · 데모·개발(DEMO 빌드).
/// [serverOnly] = 로그인 전 01 "서버 설정"에서 여는 축약판 (서버 주소 줄만).
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, this.serverOnly = false});
  final bool serverOnly;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _server;
  bool? _connected;
  bool _checking = false;
  String? _provider;
  SimState? _sim;
  bool _simDisabled = false;
  bool _scanAvailable = true;
  int? _memoryCount;

  AppSession get _s => context.read<AppSession>();

  @override
  void initState() {
    super.initState();
    _server = TextEditingController(text: context.read<AppSession>().baseUrl);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (AppConfig.demo || widget.serverOnly) _check(save: false);
      if (!widget.serverOnly) _loadMemoryCount();
    });
  }

  @override
  void dispose() {
    _server.dispose();
    super.dispose();
  }

  Future<void> _loadMemoryCount() async {
    try {
      final m = await _s.api.memory();
      if (mounted) setState(() => _memoryCount = m.items.length);
    } catch (_) {}
  }

  /// [연결 확인] → /health, /agent/info, /safety/sim (200 가상 상황 / 404 sim_disabled 실제 데이터).
  Future<void> _check({bool save = true}) async {
    setState(() => _checking = true);
    if (save) await _s.setBaseUrl(_server.text);
    final api = _s.api;
    try {
      await api.health();
      _connected = true;
    } catch (_) {
      _connected = false;
    }
    try {
      _provider = '${(await api.agentInfo())['provider'] ?? ''}';
    } catch (_) {
      _provider = null;
    }
    try {
      _sim = await api.sim();
      _s.setServerTime(_sim!.now);
      _simDisabled = false;
    } on ApiException catch (e) {
      _sim = null;
      _simDisabled = e.status == 404;
      if (_simDisabled) _s.serverOffset = Duration.zero;
    } catch (_) {
      _sim = null;
    }
    if (mounted) setState(() => _checking = false);
  }

  Future<void> _setSim(Map<String, dynamic> body, String toast) async {
    try {
      final s = await _s.api.setSim(body);
      _s.setServerTime(s.now); // 계획 목록의 오늘도 바로 따라가게
      if (!mounted) return;
      setState(() => _sim = s);
      showInfo(context, toast);
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _pickTime() async {
    final cur = (_sim?.now ?? DateTime.now()).toUtc().add(const Duration(hours: 9)); // KST 로 보여 준다
    final date = await showDatePicker(context: context, initialDate: cur, firstDate: DateTime(2026), lastDate: DateTime(2027, 12, 31));
    if (date == null || !mounted) return;
    final time = await showTimePicker(context: context, initialTime: TimeOfDay(hour: cur.hour, minute: cur.minute));
    if (time == null) return;
    final kst = DateTime.utc(date.year, date.month, date.day, time.hour, time.minute).subtract(const Duration(hours: 9));
    await _setSim({'now': kst.toIso8601String()}, '가상 시각: ${date.month}/${two(date.day)} ${two(time.hour)}:${two(time.minute)}');
  }

  Future<void> _scan() async {
    try {
      final r = await _s.api.safetyScan();
      final alerts = (r is Map ? (r['alerts'] as List?)?.length : null) ?? 0;
      if (mounted) showInfo(context, '안전 점검을 실행했어요 · 경고 $alerts건');
    } on ApiException catch (e) {
      if (e.status == 404) {
        setState(() => _scanAvailable = false); // AUTH_REQUIRED=true 면 not_available → 행 숨김
      } else if (mounted) {
        showError(context, e);
      }
    }
  }

  Future<void> _deleteAccount() async {
    final first = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('계정을 삭제할까요?'),
        content: const Text('여행 계획과 대화, 기억한 내용이 모두 지워져요.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('취소')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('계속', style: TextStyle(color: AppColors.danger))),
        ],
      ),
    );
    if (first != true || !mounted) return;
    final second = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('정말 삭제할까요?'),
        content: const Text('되돌릴 수 없어요.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('취소')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('계정 삭제', style: TextStyle(color: AppColors.danger))),
        ],
      ),
    );
    if (second != true || !mounted) return;
    try {
      await _s.deleteAccount();
    } on ApiException catch (e) {
      if (mounted) showInfo(context, e.code == 'upstream_unavailable' ? '잠시 후 다시 시도해 주세요' : userMessage(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = context.watch<AppSession>();
    final user = s.user;
    return Scaffold(
      appBar: AppBar(centerTitle: true, title: Text(widget.serverOnly ? '서버 설정' : '설정', style: AppText.headline)),
      body: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 32), children: [
        if (!widget.serverOnly) ...[
          const _GroupTitle('계정'),
          _Group(children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 8, 14),
              child: Row(children: [
                CircleAvatar(
                  radius: 20,
                  backgroundColor: AppColors.primary,
                  child: Text((user?.displayName.isNotEmpty ?? false) ? user!.displayName.characters.first : '?', style: AppText.section.copyWith(color: Colors.white)),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(user?.displayName ?? '', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
                    Text(user == null ? '' : (user.provider == 'google' ? '${user.email ?? ''} · Google' : '${user.loginId ?? ''} · 아이디 로그인'), style: AppText.caption),
                  ]),
                ),
                TextButton(onPressed: s.signOut, child: Text('로그아웃', style: AppText.label.copyWith(color: AppColors.text))),
              ]),
            ),
          ]),
          const _GroupTitle('구룡메이트'),
          _Group(children: [
            ListTile(
              leading: const Icon(Icons.bookmark_border, color: AppColors.ai),
              title: Text('나에 대해', style: AppText.label),
              trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                if (_memoryCount != null) Text('$_memoryCount개', style: AppText.caption),
                const Icon(Icons.chevron_right, color: AppColors.text3),
              ]),
              onTap: () async {
                await Navigator.of(context).push(MaterialPageRoute(builder: (_) => const MemoryScreen()));
                _loadMemoryCount();
              },
            ),
          ]),
        ],
        if (AppConfig.demo || widget.serverOnly) ...[
          Row(children: [
            const _GroupTitle('데모 · 개발'),
            if (!widget.serverOnly)
              Container(
                margin: const EdgeInsets.only(left: 6, top: 12),
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(color: AppColors.dark, borderRadius: BorderRadius.circular(8)),
                child: Text('발표용', style: AppText.caption.copyWith(color: Colors.white, fontWeight: FontWeight.w700)),
              ),
          ]),
          _Group(children: [
            if (!widget.serverOnly) ...[
              SwitchListTile(
                value: s.demoLocationMode,
                onChanged: s.setDemoLocationMode,
                activeTrackColor: AppColors.primary,
                activeThumbColor: Colors.white,
                title: Text('데모 위치 모드', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
                subtitle: Text('GPS 대신 서버에 들어온 위치(CLI·재생기)를 지도에 그려요', style: AppText.caption),
              ),
              const Divider(height: 1, indent: 16, endIndent: 16, color: AppColors.line),
            ],
            Padding(
              padding: const EdgeInsets.all(16),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('서버 주소', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
                const SizedBox(height: 8),
                TextField(
                  controller: _server,
                  keyboardType: TextInputType.url,
                  onSubmitted: (_) => _check(),
                  decoration: InputDecoration(
                    hintText: 'http://127.0.0.1:8000',
                    suffixIcon: _checking
                        ? const Padding(padding: EdgeInsets.all(14), child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)))
                        : _connected == null
                            ? null
                            : Padding(
                                padding: const EdgeInsets.only(right: 12),
                                child: Row(mainAxisSize: MainAxisSize.min, children: [
                                  Icon(_connected! ? Icons.check : Icons.close, size: 16, color: _connected! ? AppColors.success : AppColors.danger),
                                  const SizedBox(width: 2),
                                  Text(_connected! ? '연결됨' : '연결 안 됨', style: AppText.caption.copyWith(color: _connected! ? AppColors.success : AppColors.danger, fontWeight: FontWeight.w700)),
                                ]),
                              ),
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  [
                    if (_provider != null) 'agent: $_provider',
                    if (_connected == true) 'safety: ${_simDisabled ? '실제 데이터' : (_sim != null ? '가상 상황' : '확인 못 함')}',
                  ].join(' · ').ifEmpty('Android 에뮬레이터는 http://10.0.2.2:8000, 실기기는 PC 의 IP 주소'),
                  style: AppText.caption,
                ),
                const SizedBox(height: 8),
                Align(alignment: Alignment.centerRight, child: AppButton(label: '연결 확인', kind: ButtonKind.soft, height: 36, onPressed: _checking ? null : _check)),
              ]),
            ),
            if (!widget.serverOnly) ...[
              const Divider(height: 1, indent: 16, endIndent: 16, color: AppColors.line),
              _SimGroup(sim: _sim, disabled: _simDisabled || _sim == null, onScenario: (k) => _setSim({'scenario': k}, '가상 상황: ${scenarioLabels[k] ?? k}'), onTime: _setSim, onPickTime: _pickTime),
              const Divider(height: 1, indent: 16, endIndent: 16, color: AppColors.line),
              ListTile(
                leading: const Icon(Icons.place_outlined),
                title: Text('장소로 이동 (가상 위치)', style: AppText.label),
                trailing: const Icon(Icons.chevron_right, color: AppColors.text3),
                onTap: () => showInfo(context, '여행 중일 때 쓸 수 있어요. 발표는 CLI trip goto / trip replay 를 권장해요.'),
              ),
              if (_scanAvailable)
                ListTile(
                  leading: const Icon(Icons.shield_outlined),
                  title: Text('안전 점검 지금 실행', style: AppText.label),
                  trailing: const Icon(Icons.chevron_right, color: AppColors.text3),
                  onTap: _scan,
                ),
            ],
          ]),
        ],
        if (!widget.serverOnly) ...[
          const _GroupTitle('앱 정보'),
          _Group(children: [
            for (final (i, d) in LegalDoc.values.indexed) ...[
              if (i > 0) const Divider(height: 1, indent: 16, endIndent: 16, color: AppColors.line),
              ListTile(
                key: ValueKey('settings-${d.name}'),
                leading: const Icon(Icons.description_outlined, color: AppColors.text2),
                title: Text(d.title, style: AppText.label),
                trailing: const Icon(Icons.chevron_right, color: AppColors.text3),
                onTap: () => openLegalDoc(context, d),
              ),
            ],
          ]),
          const SizedBox(height: 16),
          Center(child: TextButton(onPressed: _deleteAccount, child: Text('계정 삭제', style: AppText.label.copyWith(color: AppColors.danger)))),
          Center(child: Text('구룡메이트 ${AppConfig.version}${AppConfig.demo ? ' (demo)' : ''}', style: AppText.caption)),
        ],
      ]),
    );
  }
}

extension on String {
  String ifEmpty(String other) => isEmpty ? other : this;
}

class _GroupTitle extends StatelessWidget {
  const _GroupTitle(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 14, 4, 6),
        child: Text(text, style: AppText.caption.copyWith(color: AppColors.text2, fontWeight: FontWeight.w700)),
      );
}

class _Group extends StatelessWidget {
  const _Group({required this.children});
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Container(
        decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(16)),
        clipBehavior: Clip.antiAlias,
        child: Material(type: MaterialType.transparency, child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: children)),
      );
}

/// 가상 상황 칩 + 가상 시각 (PUT /safety/sim). 404 sim_disabled 면 그룹 전체 비활성("실제 데이터 모드").
class _SimGroup extends StatelessWidget {
  const _SimGroup({required this.sim, required this.disabled, required this.onScenario, required this.onTime, required this.onPickTime});
  final SimState? sim;
  final bool disabled;
  final ValueChanged<String> onScenario;
  final Future<void> Function(Map<String, dynamic> body, String toast) onTime;
  final VoidCallback onPickTime;

  @override
  Widget build(BuildContext context) {
    final now = sim?.now.toUtc().add(const Duration(hours: 9)); // KST
    Widget chip(String label, bool on, VoidCallback? tap) => Material(
          color: on ? AppColors.primary : (disabled ? AppColors.field : AppColors.primarySoft),
          borderRadius: BorderRadius.circular(10),
          child: InkWell(
            borderRadius: BorderRadius.circular(10),
            onTap: disabled ? null : tap,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              child: Text(label, style: AppText.caption.copyWith(color: on ? Colors.white : (disabled ? AppColors.text3 : AppColors.primary), fontWeight: FontWeight.w700)),
            ),
          ),
        );
    return Opacity(
      opacity: disabled ? 0.5 : 1,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text('가상 상황', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
            if (disabled) ...[const SizedBox(width: 6), Text('실제 데이터 모드', style: AppText.caption)],
          ]),
          const SizedBox(height: 8),
          Wrap(spacing: 6, runSpacing: 6, children: [
            for (final e in scenarioLabels.entries) chip(e.value, sim?.scenario == e.key, () => onScenario(e.key)),
          ]),
          const SizedBox(height: 16),
          Text('가상 시각', style: AppText.label.copyWith(fontWeight: FontWeight.w700)),
          const SizedBox(height: 4),
          Text(now == null ? '-' : '${now.month}/${two(now.day)} (${weekdayKo(now)}) ${two(now.hour)}:${two(now.minute)} · 흐르는 중', style: AppText.meta),
          const SizedBox(height: 8),
          Wrap(spacing: 6, runSpacing: 6, children: [
            chip('시각 바꾸기', false, onPickTime),
            chip('+10분', false, () => onTime({'now': sim!.now.toUtc().add(const Duration(minutes: 10)).toIso8601String()}, '가상 시각 +10분')),
            chip('+30분', false, () => onTime({'now': sim!.now.toUtc().add(const Duration(minutes: 30)).toIso8601String()}, '가상 시각 +30분')),
            chip('실제 시각', false, () => onTime({'reset_clock': true}, '실제 시각으로 돌아왔어요')),
          ]),
        ]),
      ),
    );
  }
}
