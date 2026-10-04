// places 서비스 응답 모델 (backend/app/places/schemas.py).

double _d(Object? v) => (v as num?)?.toDouble() ?? 0;
List<String> _strs(Object? v) => (v as List?)?.map((e) => '$e').toList() ?? const [];

class PlaceSummary {
  PlaceSummary({
    required this.id,
    required this.title,
    required this.contentType,
    required this.categoryNames,
    required this.tags,
    required this.lat,
    required this.lng,
    required this.addr,
    required this.area,
    required this.thumbnailUrl,
    this.distanceM,
    this.score,
  });

  final String id;
  final String title;
  final String contentType;
  final List<String> categoryNames;
  final List<String> tags;
  final double lat;
  final double lng;
  final String addr;
  final String area;
  final String thumbnailUrl;
  final double? distanceM;
  final double? score;

  factory PlaceSummary.fromJson(Map<String, dynamic> j) => PlaceSummary(
        id: j['id'] as String,
        title: j['title'] as String? ?? '',
        contentType: j['content_type'] as String? ?? '',
        categoryNames: _strs(j['category_names']),
        tags: _strs(j['tags']),
        lat: _d(j['lat']),
        lng: _d(j['lng']),
        addr: j['addr'] as String? ?? '',
        area: j['area'] as String? ?? '',
        thumbnailUrl: j['thumbnail_url'] as String? ?? '',
        distanceM: (j['distance_m'] as num?)?.toDouble(),
        score: (j['score'] as num?)?.toDouble(),
      );

  /// 카드 메타에 쓰는 짧은 분류명 (가장 구체적인 분류).
  String get categoryLabel => categoryNames.isNotEmpty ? categoryNames.last : contentType;
}

class PlaceImage {
  PlaceImage({required this.url, required this.thumbnail});
  final String url;
  final String thumbnail;

  factory PlaceImage.fromJson(Map<String, dynamic> j) =>
      PlaceImage(url: j['url'] as String? ?? '', thumbnail: j['thumbnail'] as String? ?? '');
}

class PlaceDetail extends PlaceSummary {
  PlaceDetail({
    required super.id,
    required super.title,
    required super.contentType,
    required super.categoryNames,
    required super.tags,
    required super.lat,
    required super.lng,
    required super.addr,
    required super.area,
    required super.thumbnailUrl,
    required this.overview,
    required this.tel,
    required this.homepage,
    required this.openHours,
    required this.restDate,
    required this.parking,
    required this.fee,
    required this.menu,
    required this.imageUrl,
    required this.images,
    required this.storyCount,
  });

  final String overview;
  final String tel;
  final String homepage;
  final String openHours;
  final String restDate;
  final String parking;
  final String fee;
  final String menu;
  final String imageUrl;
  final List<PlaceImage> images;
  final int storyCount;

  factory PlaceDetail.fromJson(Map<String, dynamic> j) {
    final s = PlaceSummary.fromJson(j);
    return PlaceDetail(
      id: s.id,
      title: s.title,
      contentType: s.contentType,
      categoryNames: s.categoryNames,
      tags: s.tags,
      lat: s.lat,
      lng: s.lng,
      addr: s.addr,
      area: s.area,
      thumbnailUrl: s.thumbnailUrl,
      overview: j['overview'] as String? ?? '',
      tel: j['tel'] as String? ?? '',
      homepage: j['homepage'] as String? ?? '',
      openHours: j['open_hours'] as String? ?? '',
      restDate: j['rest_date'] as String? ?? '',
      parking: j['parking'] as String? ?? '',
      fee: j['fee'] as String? ?? '',
      menu: j['menu'] as String? ?? '',
      imageUrl: j['image_url'] as String? ?? '',
      images: ((j['images'] as List?) ?? const []).map((e) => PlaceImage.fromJson(e as Map<String, dynamic>)).toList(),
      storyCount: (j['story_count'] as num?)?.toInt() ?? 0,
    );
  }
}

class Story {
  Story({required this.id, required this.title, required this.themeTitle, required this.script, required this.playTimeSec});
  final String id;
  final String title;
  final String themeTitle;
  final String script;
  final int playTimeSec;

  factory Story.fromJson(Map<String, dynamic> j) => Story(
        id: j['id'] as String,
        title: j['title'] as String? ?? '',
        themeTitle: j['odii_theme_title'] as String? ?? '',
        script: j['script'] as String? ?? '',
        playTimeSec: (j['play_time_sec'] as num?)?.toInt() ?? 0,
      );
}

class TagCount {
  TagCount(this.tag, this.count);
  final String tag;
  final int count;

  factory TagCount.fromJson(Map<String, dynamic> j) => TagCount(j['tag'] as String, (j['count'] as num).toInt());
}
