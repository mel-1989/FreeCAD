// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include <memory>

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QGuiApplication>
#include <QImage>
#include <QPixmap>
#include <QSizeF>
#include <QString>
#include <QTemporaryDir>

#include <Gui/BitmapFactory.h>

#include <src/App/InitApplication.h>

namespace
{

QByteArray svgWithFill(const char* fill)
{
    return QByteArray("<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 10 10\" "
                      "width=\"10\" height=\"10\"><rect width=\"10\" height=\"10\" fill=\"")
        + fill + "\"/></svg>";
}

void writeFile(const QString& path, const QByteArray& content)
{
    QFile file(path);
    ASSERT_TRUE(file.open(QIODevice::WriteOnly | QIODevice::Truncate));
    file.write(content);
}

QRgb centerPixel(const QPixmap& pixmap)
{
    const QImage image = pixmap.toImage();
    return image.pixel(image.width() / 2, image.height() / 2);
}

class BitmapFactoryCacheTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
        if (!QCoreApplication::instance()) {
            static int argc = 1;
            static char arg0[] = "Gui_tests_run";
            static char* argv[] = {arg0, nullptr};
            static QGuiApplication app(argc, argv);
        }
    }

    void SetUp() override
    {
        ASSERT_TRUE(dir.isValid());
        oldSearchPaths = QDir::searchPaths(QStringLiteral("icons"));
        oldCwd = QDir::currentPath();
        QDir::addSearchPath(QStringLiteral("icons"), dir.path());
        Gui::BitmapFactory().clearCache();
    }

    void TearDown() override
    {
        QDir::setCurrent(oldCwd);
        QDir::setSearchPaths(QStringLiteral("icons"), oldSearchPaths);
        Gui::BitmapFactory().clearCache();
    }

    QString path(const QString& name) const
    {
        return dir.filePath(name);
    }

    QTemporaryDir dir;
    QStringList oldSearchPaths;
    QString oldCwd;
};

TEST_F(BitmapFactoryCacheTest, sizesAreKeyedSeparately)
{
    writeFile(path(QStringLiteral("sizes.svg")), svgWithFill("#ff0000"));

    const auto small = Gui::BitmapFactory().pixmapFromSvg("sizes", QSizeF(16, 16));
    const auto large = Gui::BitmapFactory().pixmapFromSvg("sizes", QSizeF(32, 32));

    ASSERT_FALSE(small.isNull());
    ASSERT_FALSE(large.isNull());
    EXPECT_NE(small.size(), large.size());
    EXPECT_EQ(large.width(), 2 * small.width());
    EXPECT_EQ(small.size(), Gui::BitmapFactory().pixmapFromSvg("sizes", QSizeF(16, 16)).size());
}

TEST_F(BitmapFactoryCacheTest, colorMapsAreKeyedSeparately)
{
    writeFile(path(QStringLiteral("colors.svg")), svgWithFill("#ff0000"));

    const auto plain = Gui::BitmapFactory().pixmapFromSvg("colors", QSizeF(16, 16));
    const auto mapped = Gui::BitmapFactory().pixmapFromSvg(
        "colors",
        QSizeF(16, 16),
        {{0xff0000, 0x0000ff}}
    );

    ASSERT_FALSE(plain.isNull());
    ASSERT_FALSE(mapped.isNull());
    EXPECT_EQ(centerPixel(plain), qRgba(255, 0, 0, 255));
    EXPECT_EQ(centerPixel(mapped), qRgba(0, 0, 255, 255));
}

TEST_F(BitmapFactoryCacheTest, absolutePathsAreNotCached)
{
    const QString file = path(QStringLiteral("abs.svg"));
    const QByteArray name = file.toUtf8();

    writeFile(file, svgWithFill("#ff0000"));
    const auto first = Gui::BitmapFactory().pixmapFromSvg(name.constData(), QSizeF(16, 16));
    writeFile(file, svgWithFill("#0000ff"));
    const auto second = Gui::BitmapFactory().pixmapFromSvg(name.constData(), QSizeF(16, 16));

    ASSERT_FALSE(first.isNull());
    ASSERT_FALSE(second.isNull());
    EXPECT_EQ(centerPixel(first), qRgba(255, 0, 0, 255));
    EXPECT_EQ(centerPixel(second), qRgba(0, 0, 255, 255));
}

TEST_F(BitmapFactoryCacheTest, hitReturnsTheSharedPixmap)
{
    writeFile(path(QStringLiteral("hit.svg")), svgWithFill("#ff0000"));

    const auto first = Gui::BitmapFactory().pixmapFromSvg("hit", QSizeF(16, 16));
    const auto second = Gui::BitmapFactory().pixmapFromSvg("hit", QSizeF(16, 16));

    ASSERT_FALSE(first.isNull());
    EXPECT_EQ(first.cacheKey(), second.cacheKey());
}

TEST_F(BitmapFactoryCacheTest, clearCacheDropsEntries)
{
    writeFile(path(QStringLiteral("clear.svg")), svgWithFill("#ff0000"));

    const auto first = Gui::BitmapFactory().pixmapFromSvg("clear", QSizeF(16, 16));
    ASSERT_FALSE(first.isNull());
    ASSERT_EQ(first.cacheKey(), Gui::BitmapFactory().pixmapFromSvg("clear", QSizeF(16, 16)).cacheKey());

    Gui::BitmapFactory().clearCache();

    EXPECT_NE(first.cacheKey(), Gui::BitmapFactory().pixmapFromSvg("clear", QSizeF(16, 16)).cacheKey());
}

TEST_F(BitmapFactoryCacheTest, reloadExternalThemeSettingsDropsEntries)
{
    writeFile(path(QStringLiteral("reload.svg")), svgWithFill("#ff0000"));

    const auto first = Gui::BitmapFactory().pixmapFromSvg("reload", QSizeF(16, 16));
    ASSERT_FALSE(first.isNull());
    ASSERT_EQ(first.cacheKey(), Gui::BitmapFactory().pixmapFromSvg("reload", QSizeF(16, 16)).cacheKey());

    Gui::BitmapFactory().reloadExternalThemeSettings();

    EXPECT_NE(first.cacheKey(), Gui::BitmapFactory().pixmapFromSvg("reload", QSizeF(16, 16)).cacheKey());
}

TEST_F(BitmapFactoryCacheTest, searchPathNamesAreCached)
{
    writeFile(path(QStringLiteral("sp.svg")), svgWithFill("#ff0000"));

    // a bare name found through the added "icons" search path, as the tree and link overlays use
    // (names written "icons:x" or ":/x" count as absolute paths to QDir and were never cached)
    const auto bare1 = Gui::BitmapFactory().pixmapFromSvg("sp", QSizeF(16, 16));
    const auto bare2 = Gui::BitmapFactory().pixmapFromSvg("sp", QSizeF(16, 16));
    ASSERT_FALSE(bare1.isNull());
    EXPECT_EQ(bare1.cacheKey(), bare2.cacheKey());
}

TEST_F(BitmapFactoryCacheTest, namesResolvedAgainstTheCurrentDirectoryAreNotCached)
{
    QTemporaryDir cwd;
    ASSERT_TRUE(cwd.isValid());
    ASSERT_TRUE(QDir::setCurrent(cwd.path()));
    const QString file = QStringLiteral("cwdicon.svg");

    writeFile(file, svgWithFill("#ff0000"));
    const auto first = Gui::BitmapFactory().pixmapFromSvg("cwdicon.svg", QSizeF(16, 16));
    writeFile(file, svgWithFill("#0000ff"));
    const auto second = Gui::BitmapFactory().pixmapFromSvg("cwdicon.svg", QSizeF(16, 16));

    // leave the directory before the temporary one is removed
    QDir::setCurrent(oldCwd);

    ASSERT_FALSE(first.isNull());
    ASSERT_FALSE(second.isNull());
    EXPECT_EQ(centerPixel(first), qRgba(255, 0, 0, 255));
    EXPECT_EQ(centerPixel(second), qRgba(0, 0, 255, 255));
}

}  // namespace
